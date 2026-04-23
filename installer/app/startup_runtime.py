from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from . import rtss_notice
from .archive_controller import ArchivePreparationController, ArchivePreparationState
from .game_db_controller import GameDbLoadResult
from .gpu_flow_controller import GpuFlowState
from .install_selection_controller import InstallSelectionUiState
from .runtime_state import (
    ArchiveRuntimeState,
    CardUiRuntimeState,
    GpuRuntimeState,
    InstallRuntimeState,
    SheetRuntimeState,
)


@dataclass(frozen=True)
class StartupRuntimeCallbacks:
    format_gpu_label_text: Callable[[str], str]
    set_gpu_label_text: Callable[[str], None]
    refresh_archive_info_ui: Callable[[], None]
    update_install_button_state: Callable[[], None]
    update_sheet_status: Callable[[], None]
    run_post_sheet_startup: Callable[[bool], None]
    mark_post_sheet_startup_done: Callable[[], None]
    set_scan_status_message: Callable[[str, str], None]
    clear_cards: Callable[[], None]
    set_information_text: Callable[[str], None]
    update_selected_game_header: Callable[[], None]
    apply_install_selection_state: Callable[[InstallSelectionUiState], None]
    set_folder_select_enabled: Callable[[bool], None]
    check_app_update: Callable[[], bool]
    should_apply_fsr4_for_game: Callable[[Mapping[str, Any] | None], bool]
    get_archive_controller: Callable[[], ArchivePreparationController | None]
    clear_found_games: Callable[[], None]


@dataclass(frozen=True)
class StartupRuntimeCoordinatorDeps:
    """Explicit startup-runtime inputs assembled by OptiManagerApp."""

    archive_state: ArchiveRuntimeState
    gpu_state: GpuRuntimeState
    sheet_state: SheetRuntimeState
    install_state: InstallRuntimeState
    card_ui_state: CardUiRuntimeState
    optiscaler_cache_dir: Path
    fsr4_cache_dir: Path
    optipatcher_cache_dir: Path
    specialk_cache_dir: Path
    ual_cache_dir: Path
    unreal5_cache_dir: Path
    manifest_root: Path
    callbacks: StartupRuntimeCallbacks
    unknown_gpu_text: str = "Unknown GPU"
    logger: Any = None


@dataclass(frozen=True)
class _ArchiveAssetConfig:
    entry_key: str
    cache_dir_attr: str
    prepare_method_name: str
    state_prefix: str
    source_archive_field: str
    include_manifest_root: bool = False


class StartupRuntimeCoordinator:
    def __init__(
        self,
        *,
        archive_state: ArchiveRuntimeState,
        gpu_state: GpuRuntimeState,
        sheet_state: SheetRuntimeState,
        install_state: InstallRuntimeState,
        card_ui_state: CardUiRuntimeState,
        optiscaler_cache_dir: Path,
        fsr4_cache_dir: Path,
        optipatcher_cache_dir: Path,
        specialk_cache_dir: Path,
        ual_cache_dir: Path,
        unreal5_cache_dir: Path,
        manifest_root: Path,
        unknown_gpu_text: str = "Unknown GPU",
        callbacks: StartupRuntimeCallbacks,
        logger=None,
    ) -> None:
        self._archive_state = archive_state
        self._gpu_state = gpu_state
        self._sheet_state = sheet_state
        self._install_state = install_state
        self._card_ui_state = card_ui_state
        self._optiscaler_cache_dir = Path(optiscaler_cache_dir)
        self._fsr4_cache_dir = Path(fsr4_cache_dir)
        self._optipatcher_cache_dir = Path(optipatcher_cache_dir)
        self._specialk_cache_dir = Path(specialk_cache_dir)
        self._ual_cache_dir = Path(ual_cache_dir)
        self._unreal5_cache_dir = Path(unreal5_cache_dir)
        self._manifest_root = Path(manifest_root)
        self._unknown_gpu_text = str(unknown_gpu_text or "").strip() or "Unknown GPU"
        self._callbacks = callbacks
        self._logger = logger or logging.getLogger()
        self._archive_assets = {
            "optiscaler": _ArchiveAssetConfig(
                entry_key="optiscaler",
                cache_dir_attr="_optiscaler_cache_dir",
                prepare_method_name="prepare_optiscaler",
                state_prefix="optiscaler",
                source_archive_field="opti_source_archive",
            ),
            "fsr4": _ArchiveAssetConfig(
                entry_key="fsr4int8",
                cache_dir_attr="_fsr4_cache_dir",
                prepare_method_name="prepare_fsr4",
                state_prefix="fsr4",
                source_archive_field="fsr4_source_archive",
            ),
            "optipatcher": _ArchiveAssetConfig(
                entry_key="optipatcher",
                cache_dir_attr="_optipatcher_cache_dir",
                prepare_method_name="prepare_optipatcher",
                state_prefix="optipatcher",
                source_archive_field="optipatcher_source_archive",
                include_manifest_root=True,
            ),
            "specialk": _ArchiveAssetConfig(
                entry_key="specialk",
                cache_dir_attr="_specialk_cache_dir",
                prepare_method_name="prepare_specialk",
                state_prefix="specialk",
                source_archive_field="specialk_source_archive",
                include_manifest_root=True,
            ),
            "ual": _ArchiveAssetConfig(
                entry_key="ultimateasiloader",
                cache_dir_attr="_ual_cache_dir",
                prepare_method_name="prepare_ual",
                state_prefix="ual",
                source_archive_field="ual_source_archive",
                include_manifest_root=True,
            ),
            "unreal5": _ArchiveAssetConfig(
                entry_key="unreal5",
                cache_dir_attr="_unreal5_cache_dir",
                prepare_method_name="prepare_unreal5",
                state_prefix="unreal5",
                source_archive_field="unreal5_source_archive",
                include_manifest_root=True,
            ),
        }
        self._startup_archive_prepare_order = ("fsr4", "optipatcher", "specialk", "ual", "unreal5")

    def apply_gpu_flow_state(self, state: GpuFlowState) -> None:
        gpu_state = self._gpu_state
        sheet_state = self._sheet_state
        gpu_state.gpu_context = state.gpu_context
        gpu_state.gpu_names = list(state.gpu_names or ())
        gpu_state.gpu_count = max(0, int(state.gpu_count or 0))
        gpu_state.is_multi_gpu = bool(state.is_multi_gpu)
        gpu_state.multi_gpu_blocked = bool(state.multi_gpu_blocked)
        gpu_state.gpu_selection_pending = bool(state.gpu_selection_pending)
        gpu_state.selected_adapter = state.selected_adapter

        if state.game_db_vendor is not None:
            sheet_state.active_vendor = str(state.game_db_vendor or "default")

        gpu_state.gpu_info = str(state.gpu_info or self._unknown_gpu_text).strip() or self._unknown_gpu_text
        self._callbacks.set_gpu_label_text(self._callbacks.format_gpu_label_text(gpu_state.gpu_info))

    def handle_unsupported_gpu_block(self, scan_status_message: str, info_text: str) -> None:
        sheet_state = self._sheet_state
        install_state = self._install_state
        card_ui_state = self._card_ui_state

        self._callbacks.mark_post_sheet_startup_done()
        sheet_state.loading = False
        sheet_state.status = False
        sheet_state.game_db = {}
        sheet_state.module_download_links = {}
        self._callbacks.clear_found_games()
        card_ui_state.selected_game_index = None
        install_state.popup_confirmed = False
        install_state.precheck_running = False
        install_state.precheck_ok = False
        install_state.precheck_error = ""
        install_state.precheck_dll_name = ""
        self._callbacks.apply_install_selection_state(
            InstallSelectionUiState(
                popup_confirmed=False,
                precheck_running=False,
                precheck_ok=False,
            )
        )
        self._callbacks.set_folder_select_enabled(False)
        self._callbacks.set_scan_status_message(scan_status_message, "#FF8A8A")
        self._callbacks.clear_cards()
        self._callbacks.set_information_text(info_text)
        self._callbacks.update_selected_game_header()
        self._callbacks.update_sheet_status()
        self._callbacks.update_install_button_state()

    def on_game_db_loaded(self, result: GameDbLoadResult) -> None:
        sheet_state = self._sheet_state
        gpu_state = self._gpu_state
        install_state = self._install_state
        sheet_state.loading = False
        sheet_state.active_vendor = str(result.game_db_vendor or "default")
        sheet_state.game_db = result.game_db if result.ok else {}
        sheet_state.module_download_links = result.module_download_links if result.ok else {}
        sheet_state.status = result.ok
        rtss_state = rtss_notice.probe_rtss_startup_state(logger=self._logger)
        install_state.rtss_scan_ok = bool(rtss_state.scan_ok)
        install_state.rtss_installed = bool(rtss_state.installed)
        install_state.rtss_profiles_global_exists = bool(rtss_state.profiles_global_exists)
        install_state.rtss_global_fix_needed = bool(rtss_state.global_fix_needed)

        if result.ok:
            self._logger.info(
                "[APP] Game DB loaded successfully: vendor=%s, games=%d, module_links=%d",
                sheet_state.active_vendor,
                len(sheet_state.game_db),
                len(sheet_state.module_download_links),
            )
        else:
            self._logger.error(
                "[APP] Failed to load Game DB for vendor=%s: %s",
                sheet_state.active_vendor,
                result.error,
            )

        self._callbacks.update_install_button_state()
        self._callbacks.update_sheet_status()

        if gpu_state.multi_gpu_blocked:
            return

        self._callbacks.refresh_archive_info_ui()
        update_started = self._callbacks.check_app_update() if result.ok else False
        if not update_started:
            self._callbacks.run_post_sheet_startup(result.ok)

    def start_optiscaler_archive_prepare(self) -> None:
        state = self._prepare_archive("optiscaler")
        if state is None:
            return

        self._apply_archive_state_for_asset("optiscaler", state)
        for asset_key in self._startup_archive_prepare_order:
            self._start_archive_prepare(asset_key, update_install_button_state=False)
        self._callbacks.update_install_button_state()

    def start_fsr4_archive_prepare(self) -> None:
        self._start_archive_prepare("fsr4")

    def start_optipatcher_archive_prepare(self) -> None:
        self._start_archive_prepare("optipatcher")

    def start_specialk_archive_prepare(self) -> None:
        self._start_archive_prepare("specialk")

    def start_ual_archive_prepare(self) -> None:
        self._start_archive_prepare("ual")

    def start_unreal5_archive_prepare(self) -> None:
        self._start_archive_prepare("unreal5")

    def _prepare_archive(self, asset_key: str) -> ArchivePreparationState | None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return None

        config = self._archive_assets[asset_key]
        entry = self._sheet_state.module_download_links.get(config.entry_key, {})
        cache_dir = getattr(self, config.cache_dir_attr)
        prepare_archive = getattr(controller, config.prepare_method_name)
        if asset_key == "fsr4":
            enabled = self._callbacks.should_apply_fsr4_for_game(None)
            if not enabled:
                self._logger.info("[APP] Skipping FSR4 preparation for GPU: %s", self._gpu_state.gpu_info)
            return prepare_archive(entry, cache_dir, enabled=enabled)
        if config.include_manifest_root:
            return prepare_archive(entry, cache_dir, self._manifest_root)
        return prepare_archive(entry, cache_dir)

    def _start_archive_prepare(self, asset_key: str, *, update_install_button_state: bool = True) -> None:
        state = self._prepare_archive(asset_key)
        if state is None:
            return
        self._apply_archive_state_for_asset(asset_key, state)
        if update_install_button_state:
            self._callbacks.update_install_button_state()

    def _apply_archive_state_for_asset(self, asset_key: str, state: ArchivePreparationState) -> None:
        config = self._archive_assets[asset_key]
        archive_state = self._archive_state
        setattr(archive_state, f"{config.state_prefix}_filename", str(state.filename or ""))
        setattr(archive_state, f"{config.state_prefix}_ready", bool(state.ready))
        setattr(archive_state, f"{config.state_prefix}_downloading", bool(state.downloading))
        setattr(archive_state, f"{config.state_prefix}_error", str(state.error_message or ""))
        setattr(archive_state, config.source_archive_field, str(state.archive_path or ""))

    def _on_archive_state_changed(self, asset_key: str, state: ArchivePreparationState) -> None:
        self._apply_archive_state_for_asset(asset_key, state)
        self._callbacks.update_install_button_state()

    def on_optiscaler_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("optiscaler", state)

    def on_fsr4_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("fsr4", state)

    def on_optipatcher_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("optipatcher", state)

    def on_specialk_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("specialk", state)

    def on_ual_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("ual", state)

    def on_unreal5_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self._on_archive_state_changed("unreal5", state)


def create_startup_runtime_coordinator(deps: StartupRuntimeCoordinatorDeps) -> StartupRuntimeCoordinator:
    return StartupRuntimeCoordinator(
        archive_state=deps.archive_state,
        gpu_state=deps.gpu_state,
        sheet_state=deps.sheet_state,
        install_state=deps.install_state,
        card_ui_state=deps.card_ui_state,
        optiscaler_cache_dir=deps.optiscaler_cache_dir,
        fsr4_cache_dir=deps.fsr4_cache_dir,
        optipatcher_cache_dir=deps.optipatcher_cache_dir,
        specialk_cache_dir=deps.specialk_cache_dir,
        ual_cache_dir=deps.ual_cache_dir,
        unreal5_cache_dir=deps.unreal5_cache_dir,
        manifest_root=deps.manifest_root,
        unknown_gpu_text=deps.unknown_gpu_text,
        callbacks=deps.callbacks,
        logger=deps.logger or logging.getLogger(),
    )


__all__ = [
    "StartupRuntimeCallbacks",
    "StartupRuntimeCoordinator",
    "StartupRuntimeCoordinatorDeps",
    "create_startup_runtime_coordinator",
]
