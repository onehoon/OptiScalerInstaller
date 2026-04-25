from __future__ import annotations

import logging
from concurrent.futures import Executor, Future
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from ..common.log_sanitizer import redact_text


@dataclass(slots=True)
class PosterQueueJob:
    index: int
    label: object
    title: str
    cover_filename: str
    url: str
    generation: int
    delayed_retry_count: int = 0


class PosterQueueController:
    def __init__(
        self,
        *,
        root: Any,
        executor: Executor,
        loader: Callable[[str, str, str], Any],
        max_workers: int,
        retry_delay_ms: int,
        get_visible_indices: Callable[[], set[int]],
        is_scan_in_progress: Callable[[], bool],
        on_image_ready: Callable[[int, object, Any], None],
    ) -> None:
        self._root = root
        self._executor = executor
        self._loader = loader
        self._max_workers = max(1, int(max_workers))
        self._retry_delay_ms = max(0, int(retry_delay_ms))
        self._get_visible_indices = get_visible_indices
        self._is_scan_in_progress = is_scan_in_progress
        self._on_image_ready = on_image_ready

        self._pending_jobs: dict[int, PosterQueueJob] = {}
        self._inflight_jobs: dict[Future[Any], PosterQueueJob] = {}
        self._failed_jobs: dict[int, PosterQueueJob] = {}
        self._delayed_retry_after_ids: dict[int, str] = {}
        self._render_generation = 0
        self._image_queue_after_id: Optional[str] = None
        self._initial_image_pass = True
        self._retry_attempted = False

    def begin_new_render(self) -> None:
        self._render_generation += 1
        self._pending_jobs.clear()
        self._inflight_jobs.clear()
        self._failed_jobs.clear()
        self._cancel_image_queue_tick()
        for index in list(self._delayed_retry_after_ids.keys()):
            self._cancel_delayed_retry(index)
        self._initial_image_pass = True
        self._retry_attempted = False

    def shutdown(self) -> None:
        self._cancel_image_queue_tick()
        for index in list(self._delayed_retry_after_ids.keys()):
            self._cancel_delayed_retry(index)
        self._pending_jobs.clear()
        self._inflight_jobs.clear()
        self._failed_jobs.clear()

    def queue(self, index: int, label: object, title: str, cover_filename: str, url: str) -> None:
        self._cancel_delayed_retry(index)
        self._pending_jobs[index] = PosterQueueJob(
            index=index,
            label=label,
            title=title,
            cover_filename=cover_filename,
            url=url,
            generation=self._render_generation,
        )
        self.pump()

    def pump(self) -> None:
        self._collect_completed_jobs()

        if not self._pending_jobs and not self._inflight_jobs and not self._is_scan_in_progress():
            self._initial_image_pass = False

        if (
            not self._pending_jobs
            and not self._inflight_jobs
            and not self._is_scan_in_progress()
            and self._failed_jobs
            and not self._retry_attempted
        ):
            self._retry_attempted = True
            logging.info("Retrying %d failed poster download(s)...", len(self._failed_jobs))
            for index, job in list(self._failed_jobs.items()):
                self._pending_jobs[index] = replace(job, generation=self._render_generation)
            self._failed_jobs.clear()

        visible = self._get_visible_indices()

        while self._pending_jobs and len(self._inflight_jobs) < self._max_workers:
            next_index = min(self._pending_jobs.keys(), key=lambda idx: self._image_priority_key(idx, visible))
            job = self._pending_jobs.pop(next_index)
            if job.generation != self._render_generation:
                continue

            future = self._executor.submit(
                self._loader,
                job.title,
                job.cover_filename,
                job.url,
            )
            self._inflight_jobs[future] = job

        if (self._pending_jobs or self._inflight_jobs) and self._image_queue_after_id is None:
            self._image_queue_after_id = self._root.after(120, self._image_queue_tick)

    def _cancel_image_queue_tick(self) -> None:
        if self._image_queue_after_id is None:
            return
        try:
            self._root.after_cancel(self._image_queue_after_id)
        except Exception:
            pass
        self._image_queue_after_id = None

    def _cancel_delayed_retry(self, index: int) -> None:
        after_id = self._delayed_retry_after_ids.pop(index, None)
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except Exception:
            pass

    def _schedule_delayed_retry(self, job: PosterQueueJob) -> None:
        index = int(job.index)
        if index < 0:
            return
        if int(job.delayed_retry_count) >= 1:
            return
        if index in self._delayed_retry_after_ids:
            return

        retry_job = replace(job, delayed_retry_count=job.delayed_retry_count + 1)

        def _requeue() -> None:
            self._delayed_retry_after_ids.pop(index, None)
            try:
                if not self._root.winfo_exists():
                    return
            except Exception:
                return

            if retry_job.generation != self._render_generation:
                return

            self._pending_jobs[index] = retry_job
            self.pump()

        try:
            after_id = self._root.after(self._retry_delay_ms, _requeue)
        except Exception:
            return
        self._delayed_retry_after_ids[index] = after_id

    def _image_priority_key(self, index: int, visible: set[int]) -> tuple[int, ...]:
        if self._initial_image_pass:
            return (index,)

        is_visible = 0 if index in visible else 1
        if visible:
            nearest = min(abs(index - visible_index) for visible_index in visible)
        else:
            nearest = index
        return (is_visible, nearest, index)

    def _collect_completed_jobs(self) -> None:
        completed: list[tuple[Future[Any], PosterQueueJob]] = []
        for future, job in list(self._inflight_jobs.items()):
            if future.done():
                completed.append((future, job))
                self._inflight_jobs.pop(future, None)

        for future, job in completed:
            try:
                load_result = future.result()
                if job.generation != self._render_generation:
                    continue
                self._on_image_ready(job.index, job.label, load_result.image)
                if load_result.should_retry:
                    self._schedule_delayed_retry(job)
            except Exception as exc:
                logging.warning("Poster download failed (will retry): %s", redact_text(exc))
                if job.generation == self._render_generation:
                    self._failed_jobs[job.index] = job

    def _image_queue_tick(self) -> None:
        self._image_queue_after_id = None
        self.pump()
