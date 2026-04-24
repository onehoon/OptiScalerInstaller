from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Executor
from dataclasses import dataclass
import logging
import threading
from typing import Any

from ..games import scanner as game_scanner
from ..games.scan_hints import save_manual_scan_hint
from ..i18n import Lang
from ..common import schedule_safely


_SCAN_MAX_WORKERS = 4


def _group_paths_by_drive(paths: list[str], max_groups: int) -> list[list[str]]:
    """Group scan paths by drive letter, then distribute groups across at most max_groups buckets."""
    drive_map: dict[str, list[str]] = {}
    for path in paths:
        # Use first two chars (e.g. "D:") as drive key; fall back to "" for UNC/relative paths
        drive_key = path[:2].upper() if len(path) >= 2 and path[1] == ":" else ""
        drive_map.setdefault(drive_key, []).append(path)

    drive_groups = list(drive_map.values())
    if len(drive_groups) <= max_groups:
        return drive_groups

    # More drive groups than max_groups: merge smallest groups round-robin until within limit
    buckets: list[list[str]] = [[] for _ in range(max_groups)]
    for i, group in enumerate(drive_groups):
        buckets[i % max_groups].extend(group)
    return [b for b in buckets if b]



LangProvider = Callable[[], Lang]
GameDbProvider = Callable[[], dict[str, dict[str, Any]]]
GameSupportPredicate = Callable[[dict[str, Any]], bool]
SchedulerCallback = Callable[[Callable[[], None]], Any]


@dataclass(frozen=True)
class ScanControllerCallbacks:
    prepare_scan_ui: Callable[[], None]
    reset_scan_results: Callable[[], None]
    add_game_card: Callable[[dict[str, Any]], None]
    finish_scan_ui: Callable[[], None]
    pump_poster_queue: Callable[[], None]
    show_auto_scan_empty_popup: Callable[[], None]
    show_manual_scan_empty_popup: Callable[[], None]
    show_select_game_hint: Callable[[], None]


class ScanController:
    def __init__(
        self,
        *,
        executor: Executor,
        schedule: SchedulerCallback,
        callbacks: ScanControllerCallbacks,
        get_game_db: GameDbProvider,
        get_lang: LangProvider,
        is_game_supported: GameSupportPredicate,
        logger=None,
    ) -> None:
        self._executor = executor
        self._schedule = schedule
        self._callbacks = callbacks
        self._get_game_db = get_game_db
        self._get_lang = get_lang
        self._is_game_supported = is_game_supported
        self._logger = logger or logging.getLogger()

        self._scan_in_progress = False
        self._auto_scan_active = False
        self._scan_generation = 0
        self._found_count = 0
        self._pending_workers = 0
        self._pending_lock = threading.Lock()

    @property
    def is_scan_in_progress(self) -> bool:
        return self._scan_in_progress

    def _normalize_scan_paths(self, scan_paths: Iterable[str]) -> list[str]:
        return [str(path or "").strip() for path in scan_paths if str(path or "").strip()]

    def _can_start_scan(self, normalized_paths: list[str]) -> bool:
        return bool(normalized_paths) and not self._scan_in_progress

    def start_auto_scan(self) -> bool:
        if self._scan_in_progress:
            return False

        scan_paths = game_scanner.get_auto_scan_paths(logger=self._logger)
        if not scan_paths:
            self._callbacks.show_auto_scan_empty_popup()
            return False

        return self.start_scan(scan_paths, is_auto=True)

    def start_manual_scan(self, folder_path: str) -> bool:
        normalized_paths = self._normalize_scan_paths((folder_path,))
        if not self._can_start_scan(normalized_paths):
            return False
        normalized_path = normalized_paths[0]
        save_manual_scan_hint(normalized_path, logger=self._logger)
        return self.start_scan(normalized_paths, is_auto=False)

    def start_scan(self, scan_paths: Iterable[str], *, is_auto: bool) -> bool:
        normalized_paths = self._normalize_scan_paths(scan_paths)
        if not self._can_start_scan(normalized_paths):
            return False

        self._scan_generation += 1
        generation = self._scan_generation
        self._scan_in_progress = True
        self._auto_scan_active = bool(is_auto)
        self._found_count = 0

        self._callbacks.reset_scan_results()
        self._callbacks.prepare_scan_ui()

        path_groups = _group_paths_by_drive(normalized_paths, _SCAN_MAX_WORKERS)
        game_db = dict(self._get_game_db() or {})
        lang = self._get_lang()

        try:
            for group in path_groups:
                # Count the worker before submit so a very fast worker cannot
                # finish and decrement the counter before the main thread
                # records it as pending.
                with self._pending_lock:
                    self._pending_workers += 1
                try:
                    self._executor.submit(
                        self._run_scan_worker,
                        generation,
                        tuple(group),
                        game_db,
                        lang,
                    )
                except Exception:
                    with self._pending_lock:
                        self._pending_workers -= 1
                    raise
        except Exception:
            self._logger.exception("Failed to submit scan worker")
            self._scan_generation += 1
            self._scan_in_progress = False
            self._auto_scan_active = False
            self._callbacks.finish_scan_ui()
            self._callbacks.pump_poster_queue()
            return False

        return True

    def _run_scan_worker(
        self,
        generation: int,
        scan_paths: tuple[str, ...],
        game_db: dict[str, dict[str, Any]],
        lang: Lang,
    ) -> None:
        try:
            for game in game_scanner.iter_scan_game_folders(
                scan_paths,
                game_db,
                lang=lang,
                is_game_supported=self._is_game_supported,
                logger=self._logger,
            ):
                self._schedule_callback(
                    lambda game_record=game, scheduled_generation=generation: self._on_game_found(
                        scheduled_generation,
                        game_record,
                    ),
                    description="found-game callback",
                )
        except Exception:
            self._logger.exception("Scan worker error")
        finally:
            with self._pending_lock:
                self._pending_workers -= 1
                is_last = self._pending_workers == 0

            if is_last:
                self._schedule_callback(
                    lambda scheduled_generation=generation: self._on_scan_complete(scheduled_generation),
                    description="scan completion callback",
                )

    def _schedule_callback(self, callback: Callable[[], None], *, description: str) -> None:
        schedule_safely(self._schedule, callback, self._logger, description=description)

    def _on_game_found(self, generation: int, game: dict[str, Any]) -> None:
        if generation != self._scan_generation:
            return

        self._found_count += 1
        self._callbacks.add_game_card(game)

    def _on_scan_complete(self, generation: int) -> None:
        if generation != self._scan_generation:
            return

        was_auto = self._auto_scan_active
        self._scan_in_progress = False
        self._auto_scan_active = False

        self._callbacks.finish_scan_ui()

        if self._found_count > 0:
            self._callbacks.show_select_game_hint()
        elif was_auto:
            self._callbacks.show_auto_scan_empty_popup()
        else:
            self._callbacks.show_manual_scan_empty_popup()

        self._callbacks.pump_poster_queue()
