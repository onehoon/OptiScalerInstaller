from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

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
        default_sheet_gid: int,
        unknown_gpu_text: str,
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
        self._default_sheet_gid = int(default_sheet_gid)
        self._unknown_gpu_text = str(unknown_gpu_text or "").strip() or "Unknown GPU"
        self._callbacks = callbacks
        self._logger = logger or logging.getLogger()

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
        if state.game_db_gid is not None:
            sheet_state.active_gid = int(state.game_db_gid or self._default_sheet_gid)

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
        sheet_state.loading = False
        sheet_state.active_gid = int(result.game_db_gid)
        sheet_state.active_vendor = str(result.game_db_vendor or "default")
        sheet_state.game_db = result.game_db if result.ok else {}
        sheet_state.module_download_links = result.module_download_links if result.ok else {}
        sheet_state.status = result.ok

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
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        entry = self._sheet_state.module_download_links.get("optiscaler", {})
        state = controller.prepare_optiscaler(entry, self._optiscaler_cache_dir)
        self.apply_optiscaler_archive_state(state)
        self.start_fsr4_archive_prepare()
        self.start_optipatcher_archive_prepare()
        self.start_specialk_archive_prepare()
        self.start_ual_archive_prepare()
        self.start_unreal5_archive_prepare()
        self._callbacks.update_install_button_state()

    def start_fsr4_archive_prepare(self) -> None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        enabled = self._callbacks.should_apply_fsr4_for_game(None)
        if not enabled:
            self._logger.info("[APP] Skipping FSR4 preparation for GPU: %s", self._gpu_state.gpu_info)

        entry = self._sheet_state.module_download_links.get("fsr4int8", {})
        state = controller.prepare_fsr4(
            entry,
            self._fsr4_cache_dir,
            enabled=enabled,
        )
        self.apply_fsr4_archive_state(state)
        self._callbacks.update_install_button_state()

    def start_optipatcher_archive_prepare(self) -> None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        entry = self._sheet_state.module_download_links.get("optipatcher", {})
        state = controller.prepare_optipatcher(entry, self._optipatcher_cache_dir, self._manifest_root)
        self.apply_optipatcher_archive_state(state)
        self._callbacks.update_install_button_state()

    def start_specialk_archive_prepare(self) -> None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        entry = self._sheet_state.module_download_links.get("specialk", {})
        state = controller.prepare_specialk(entry, self._specialk_cache_dir, self._manifest_root)
        self.apply_specialk_archive_state(state)
        self._callbacks.update_install_button_state()

    def start_ual_archive_prepare(self) -> None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        entry = self._sheet_state.module_download_links.get("ultimateasiloader", {})
        state = controller.prepare_ual(entry, self._ual_cache_dir, self._manifest_root)
        self.apply_ual_archive_state(state)
        self._callbacks.update_install_button_state()

    def start_unreal5_archive_prepare(self) -> None:
        controller = self._callbacks.get_archive_controller()
        if controller is None:
            return

        entry = self._sheet_state.module_download_links.get("unreal5", {})
        state = controller.prepare_unreal5(entry, self._unreal5_cache_dir, self._manifest_root)
        self.apply_unreal5_archive_state(state)
        self._callbacks.update_install_button_state()

    def _apply_archive_state(
        self, state: ArchivePreparationState, prefix: str, source_archive_field: str
    ) -> None:
        archive_state = self._archive_state
        setattr(archive_state, f"{prefix}_filename", str(state.filename or ""))
        setattr(archive_state, f"{prefix}_ready", bool(state.ready))
        setattr(archive_state, f"{prefix}_downloading", bool(state.downloading))
        setattr(archive_state, f"{prefix}_error", str(state.error_message or ""))
        setattr(archive_state, source_archive_field, str(state.archive_path or ""))

    def apply_optiscaler_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "optiscaler", "opti_source_archive")

    def apply_fsr4_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "fsr4", "fsr4_source_archive")

    def apply_optipatcher_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "optipatcher", "optipatcher_source_archive")

    def apply_specialk_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "specialk", "specialk_source_archive")

    def apply_ual_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "ual", "ual_source_archive")

    def apply_unreal5_archive_state(self, state: ArchivePreparationState) -> None:
        self._apply_archive_state(state, "unreal5", "unreal5_source_archive")

    def on_optiscaler_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_optiscaler_archive_state(state)
        self._callbacks.update_install_button_state()

    def on_fsr4_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_fsr4_archive_state(state)
        self._callbacks.update_install_button_state()

    def on_optipatcher_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_optipatcher_archive_state(state)
        self._callbacks.update_install_button_state()

    def on_specialk_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_specialk_archive_state(state)
        self._callbacks.update_install_button_state()

    def on_ual_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_ual_archive_state(state)
        self._callbacks.update_install_button_state()

    def on_unreal5_archive_state_changed(self, state: ArchivePreparationState) -> None:
        self.apply_unreal5_archive_state(state)
        self._callbacks.update_install_button_state()


def _set_widget_text_if_present(widget: Any, text: str) -> None:
    if widget is None:
        return
    if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
        return
    widget.configure(text=str(text or ""))


def _set_widget_enabled_if_present(widget: Any, enabled: bool) -> None:
    if widget is None:
        return
    if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
        return
    widget.configure(state="normal" if enabled else "disabled")


def create_startup_runtime_coordinator(app: Any, *, default_sheet_gid: int) -> StartupRuntimeCoordinator:
    return StartupRuntimeCoordinator(
        archive_state=app.archive_state,
        gpu_state=app.gpu_state,
        sheet_state=app.sheet_state,
        install_state=app.install_state,
        card_ui_state=app.card_ui_state,
        optiscaler_cache_dir=app.optiscaler_cache_dir,
        fsr4_cache_dir=app.fsr4_cache_dir,
        optipatcher_cache_dir=app.optipatcher_cache_dir,
        specialk_cache_dir=app.specialk_cache_dir,
        ual_cache_dir=app.ual_cache_dir,
        unreal5_cache_dir=app.unreal5_cache_dir,
        manifest_root=app.manifest_root,
        default_sheet_gid=default_sheet_gid,
        unknown_gpu_text=app.txt.main.unknown_gpu,
        callbacks=StartupRuntimeCallbacks(
            format_gpu_label_text=app._format_gpu_label_text,
            set_gpu_label_text=lambda text: _set_widget_text_if_present(getattr(app, "gpu_lbl", None), text),
            refresh_archive_info_ui=app._refresh_optiscaler_archive_info_ui,
            update_install_button_state=app._update_install_button_state,
            update_sheet_status=app._update_sheet_status,
            run_post_sheet_startup=lambda ok: getattr(app, "_startup_flow", None).run_post_sheet_startup(ok)
            if getattr(app, "_startup_flow", None) is not None
            else None,
            mark_post_sheet_startup_done=lambda: getattr(app, "_startup_flow", None).mark_post_sheet_startup_done()
            if getattr(app, "_startup_flow", None) is not None
            else None,
            set_scan_status_message=app._set_scan_status_message,
            clear_cards=app._clear_cards,
            set_information_text=lambda text: app._set_information_text(text)
            if getattr(app, "info_text", None)
            else None,
            update_selected_game_header=app._update_selected_game_header,
            apply_install_selection_state=app._apply_install_selection_state,
            set_folder_select_enabled=lambda enabled: _set_widget_enabled_if_present(
                getattr(app, "btn_select_folder", None),
                enabled,
            ),
            check_app_update=app.check_app_update,
            should_apply_fsr4_for_game=app._should_apply_fsr4_for_game,
            get_archive_controller=lambda: getattr(app, "_archive_controller", None),
            clear_found_games=lambda: setattr(app, "found_exe_list", []),
        ),
        logger=logging.getLogger(),
    )


__all__ = [
    "StartupRuntimeCallbacks",
    "StartupRuntimeCoordinator",
    "create_startup_runtime_coordinator",
]
