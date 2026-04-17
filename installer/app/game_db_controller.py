from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
import logging
from typing import Any

from ..data import gpu_bundle_loader


SchedulerCallback = Callable[[Callable[[], None]], Any]
GameDbLoader = Callable[[int], dict[str, dict[str, Any]]]
ModuleLinksLoader = Callable[[], dict[str, Any]]
GpuBundleLoader = Callable[[str, str, str], dict[str, dict[str, Any]]]
GpuBundleMerger = Callable[[dict[str, dict[str, Any]], dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class GameDbLoadResult:
    game_db: dict[str, dict[str, Any]]
    module_download_links: dict[str, Any]
    ok: bool
    error: Exception | None
    game_db_gid: int
    game_db_vendor: str


@dataclass(frozen=True)
class GameDbControllerCallbacks:
    on_load_complete: Callable[[GameDbLoadResult], None]


class GameDbLoadController:
    def __init__(
        self,
        *,
        executor: Executor,
        schedule: SchedulerCallback,
        callbacks: GameDbControllerCallbacks,
        load_game_db: GameDbLoader,
        load_module_download_links: ModuleLinksLoader,
        gpu_bundle_url: str = "",
        load_gpu_bundle: GpuBundleLoader = gpu_bundle_loader.load_supported_game_bundle,
        merge_gpu_bundle: GpuBundleMerger = gpu_bundle_loader.merge_gpu_bundle_into_game_db,
        logger=None,
    ) -> None:
        self._executor = executor
        self._schedule = schedule
        self._callbacks = callbacks
        self._load_game_db = load_game_db
        self._load_module_download_links = load_module_download_links
        self._gpu_bundle_url = str(gpu_bundle_url or "").strip()
        self._load_gpu_bundle = load_gpu_bundle
        self._merge_gpu_bundle = merge_gpu_bundle
        self._logger = logger or logging.getLogger()

        self._load_started = False

    def start_load(self, game_db_gid: int, game_db_vendor: str, gpu_model: str = "") -> bool:
        if self._load_started:
            return False

        self._load_started = True
        normalized_gid = int(game_db_gid)
        normalized_vendor = str(game_db_vendor or "default")
        normalized_gpu_model = str(gpu_model or "").strip()

        try:
            self._executor.submit(self._run_load_worker, normalized_gid, normalized_vendor, normalized_gpu_model)
        except Exception as exc:
            self._logger.exception("Failed to submit game DB load worker")
            self._schedule_result(
                GameDbLoadResult(
                    game_db={},
                    module_download_links={},
                    ok=False,
                    error=exc,
                    game_db_gid=normalized_gid,
                    game_db_vendor=normalized_vendor,
                ),
                description="game DB load failure callback",
            )
            return False

        return True

    def _run_load_worker(self, game_db_gid: int, game_db_vendor: str, gpu_model: str = "") -> None:
        try:
            game_db = self._load_game_db(game_db_gid)
            if not game_db:
                raise ValueError("Game DB has no data.")

            # GPU bundle enrichment is optional and must never fail startup flow.
            if self._gpu_bundle_url and game_db_vendor and game_db_vendor != "default":
                try:
                    bundle = self._load_gpu_bundle(self._gpu_bundle_url, game_db_vendor, gpu_model)
                    game_db = self._merge_gpu_bundle(game_db, bundle)
                except Exception as bundle_err:
                    self._logger.warning("Failed to load GPU bundle: %s", bundle_err)

            module_links: dict[str, Any] = {}
            try:
                module_links = self._load_module_download_links()
            except Exception as link_err:
                self._logger.warning(
                    "Failed to load module download links: %s",
                    link_err,
                )

            result = GameDbLoadResult(
                game_db=game_db,
                module_download_links=module_links,
                ok=True,
                error=None,
                game_db_gid=game_db_gid,
                game_db_vendor=game_db_vendor,
            )
        except Exception as exc:
            result = GameDbLoadResult(
                game_db={},
                module_download_links={},
                ok=False,
                error=exc,
                game_db_gid=game_db_gid,
                game_db_vendor=game_db_vendor,
            )

        self._schedule_result(result, description="game DB load completion callback")

    def _schedule_result(self, result: GameDbLoadResult, *, description: str) -> None:
        try:
            self._schedule(lambda load_result=result: self._callbacks.on_load_complete(load_result))
        except Exception:
            self._logger.exception("Failed to schedule %s", description)
