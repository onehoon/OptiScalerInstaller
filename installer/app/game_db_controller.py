from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
import logging
from typing import Any

from ..data import gpu_bundle_loader, message_loader, profile_loader


SchedulerCallback = Callable[[Callable[[], None]], Any]
GameDbLoader = Callable[[int], dict[str, dict[str, Any]]]
ModuleLinksLoader = Callable[[], dict[str, Any]]
MessageCenterLoader = Callable[[str], dict[str, message_loader.MessageTemplate]]
MessageBindingLoader = Callable[[str], tuple[message_loader.MessageBinding, ...]]
MessageRepoBuilder = Callable[[dict[str, message_loader.MessageTemplate], tuple[message_loader.MessageBinding, ...]], message_loader.MessageRepository]
MessageMaterializer = Callable[[dict[str, dict[str, Any]], message_loader.MessageRepository], dict[str, dict[str, Any]]]
GpuBundleLoader = Callable[[str, str, str], dict[str, dict[str, Any]]]
GpuBundleMerger = Callable[[dict[str, dict[str, Any]], dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]
ProfileCatalogLoader = Callable[[str, str, str, str], profile_loader.ProfileCatalogs]
ProfileCatalogAttacher = Callable[[dict[str, dict[str, Any]], profile_loader.ProfileCatalogs], dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class GameDbLoadResult:
    game_db: dict[str, dict[str, Any]]
    ok: bool
    error: Exception | None
    module_download_links: dict[str, Any] = field(default_factory=dict)
    game_db_gid: int = 0
    game_db_vendor: str = "default"
    # Backward-compatible aliases used by legacy tests/callers
    resource_master: dict[str, Any] = field(default_factory=dict)
    startup_warning_text: str = ""

    def __post_init__(self) -> None:
        # Legacy callers pass resource_master; normalize to module_download_links.
        if self.resource_master and not self.module_download_links:
            object.__setattr__(self, "module_download_links", dict(self.resource_master))


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
        message_center_url: str = "",
        message_binding_url: str = "",
        load_message_center: MessageCenterLoader = message_loader.load_message_center,
        load_message_binding: MessageBindingLoader = message_loader.load_message_binding,
        build_message_repository: MessageRepoBuilder = message_loader.build_message_repository,
        materialize_bound_messages: MessageMaterializer = message_loader.materialize_bound_messages_into_game_db,
        gpu_bundle_url: str = "",
        load_gpu_bundle: GpuBundleLoader = gpu_bundle_loader.load_supported_game_bundle,
        merge_gpu_bundle: GpuBundleMerger = gpu_bundle_loader.merge_gpu_bundle_into_game_db,
        game_ini_profile_url: str = "",
        engine_ini_profile_url: str = "",
        game_xml_profile_url: str = "",
        registry_profile_url: str = "",
        load_profile_catalogs: ProfileCatalogLoader = profile_loader.load_profile_catalogs,
        attach_profile_catalogs: ProfileCatalogAttacher = profile_loader.attach_profile_catalogs_to_game_db,
        message_lang: str = "en",
        logger=None,
    ) -> None:
        self._executor = executor
        self._schedule = schedule
        self._callbacks = callbacks
        self._load_game_db = load_game_db
        self._load_module_download_links = load_module_download_links
        self._message_center_url = str(message_center_url or "").strip()
        self._message_binding_url = str(message_binding_url or "").strip()
        self._load_message_center = load_message_center
        self._load_message_binding = load_message_binding
        self._build_message_repository = build_message_repository
        self._materialize_bound_messages = materialize_bound_messages
        self._gpu_bundle_url = str(gpu_bundle_url or "").strip()
        self._load_gpu_bundle = load_gpu_bundle
        self._merge_gpu_bundle = merge_gpu_bundle
        self._game_ini_profile_url = str(game_ini_profile_url or "").strip()
        self._engine_ini_profile_url = str(engine_ini_profile_url or "").strip()
        self._game_xml_profile_url = str(game_xml_profile_url or "").strip()
        self._registry_profile_url = str(registry_profile_url or "").strip()
        self._load_profile_catalogs = load_profile_catalogs
        self._attach_profile_catalogs = attach_profile_catalogs
        self._message_lang = str(message_lang or "en").strip() or "en"
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

            message_center = self._load_message_center(self._message_center_url)
            message_binding = self._load_message_binding(self._message_binding_url)
            message_repo = self._build_message_repository(message_center, message_binding)
            game_db = self._materialize_bound_messages(
                game_db,
                message_repo,
                gpu_vendor=game_db_vendor,
            )

            # GPU bundle is vendor-specific runtime data; if configured, a failed fetch must fail closed.
            if self._gpu_bundle_url and game_db_vendor and game_db_vendor != "default":
                try:
                    bundle = self._load_gpu_bundle(self._gpu_bundle_url, game_db_vendor, gpu_model)
                    game_db = self._merge_gpu_bundle(game_db, bundle)
                except Exception as bundle_err:
                    self._logger.error("Failed to load GPU bundle: %s", bundle_err)
                    raise RuntimeError("GPU bundle load failed") from bundle_err

            if (
                self._game_ini_profile_url
                and self._engine_ini_profile_url
                and self._game_xml_profile_url
                and self._registry_profile_url
            ):
                try:
                    catalogs = self._load_profile_catalogs(
                        self._game_ini_profile_url,
                        self._engine_ini_profile_url,
                        self._game_xml_profile_url,
                        self._registry_profile_url,
                    )
                    game_db = self._attach_profile_catalogs(game_db, catalogs)
                except Exception as profile_err:
                    self._logger.error("Failed to load profile catalogs: %s", profile_err)
                    raise RuntimeError("Profile catalog load failed") from profile_err

            module_links = self._load_module_download_links()

            warning_ko = message_loader.resolve_startup_warning_text(
                message_repo,
                gpu_vendor=game_db_vendor,
                lang="ko",
            )
            warning_en = message_loader.resolve_startup_warning_text(
                message_repo,
                gpu_vendor=game_db_vendor,
                lang="en",
            )
            if warning_ko:
                module_links["__warning_kr__"] = warning_ko
            if warning_en:
                module_links["__warning_en__"] = warning_en

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
