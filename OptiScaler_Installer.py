import os
import logging
import sys
from pathlib import Path
from typing import Optional
from installer.app.bootstrap_runtime import (
    APP_VERSION,
    MAX_SUPPORTED_GPU_COUNT,
    configure_logging,
    get_bool_env,
    get_int_env,
    get_prefixed_logger,
    get_runtime_config_value,
    load_dev_env_file,
)
from installer.app.composition_config import build_app_composition_config
from installer.app.install_flow import InstallFlowController
from installer.app.install_selection_controller import (
    InstallSelectionUiState,
)
from installer.app.path_config import build_app_path_config
from installer.app.runtime_builders import (
    configure_app_startup_window,
    initialize_app_infra,
    initialize_app_presenters,
    initialize_app_runtime_startup,
    initialize_app_runtime_state,
)
from installer.app.game_support_policy import parse_support_flag
from installer.app.install_state import build_install_button_state_inputs, build_selected_game_snapshot
from installer.app.install_ui_state import InstallButtonStateInputs, compute_install_button_state
from installer.app.runtime_state import (
    get_runtime_state_attr,
    set_runtime_state_attr,
)
from installer.app.scan_entry_controller import ScanEntryState
from installer.app.window_focus import has_startup_foreground_request, request_window_foreground
from installer.app.ui_runtime_config import (
    build_app_ui_runtime_config,
)
from installer.i18n import (
    detect_ui_language,
    get_app_strings,
    is_korean,
)
from installer.system import gpu_service

try:
    import customtkinter as ctk
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "customtkinter is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install customtkinter"
    ) from e

try:
    from PIL import Image
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "Pillow (PIL) is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install Pillow"
    ) from e

try:
    from dotenv import load_dotenv
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "python-dotenv is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install python-dotenv"
    ) from e


load_dev_env_file(load_dotenv, entry_file=__file__)

# Allow overriding these values via build-time config for frozen builds
# and via environment variables/.env during source development.
SUPPORTED_GAMES_WIKI_URL = get_runtime_config_value("SUPPORTED_GAMES_WIKI_URL", "").strip()
OPTISCALER_GPU_BUNDLE_URL = get_runtime_config_value("OPTISCALER_GPU_BUNDLE_URL", "").strip()
OPTISCALER_GPU_BUNDLE_DEBUG = get_bool_env("OPTISCALER_GPU_BUNDLE_DEBUG", False)
OPTISCALER_GAME_MASTER_URL = get_runtime_config_value("OPTISCALER_GAME_MASTER_URL", "").strip()
OPTISCALER_RESOURCE_MASTER_URL = get_runtime_config_value("OPTISCALER_RESOURCE_MASTER_URL", "").strip()
OPTISCALER_MESSAGE_CENTER_URL = get_runtime_config_value("OPTISCALER_MESSAGE_CENTER_URL", "").strip()
OPTISCALER_MESSAGE_BINDING_URL = get_runtime_config_value("OPTISCALER_MESSAGE_BINDING_URL", "").strip()
OPTISCALER_GAME_INI_PROFILE_URL = get_runtime_config_value("OPTISCALER_GAME_INI_PROFILE_URL", "").strip()
OPTISCALER_GAME_UNREAL_INI_PROFILE_URL = get_runtime_config_value("OPTISCALER_GAME_UNREAL_INI_PROFILE_URL", "").strip()
OPTISCALER_ENGINE_INI_PROFILE_URL = get_runtime_config_value("OPTISCALER_ENGINE_INI_PROFILE_URL", "").strip()
OPTISCALER_GAME_XML_PROFILE_URL = get_runtime_config_value("OPTISCALER_GAME_XML_PROFILE_URL", "").strip()
OPTISCALER_REGISTRY_PROFILE_URL = get_runtime_config_value("OPTISCALER_REGISTRY_PROFILE_URL", "").strip()
OPTISCALER_GAME_JSON_PROFILE_URL = get_runtime_config_value("OPTISCALER_GAME_JSON_PROFILE_URL", "").strip()

configure_logging(app_version=APP_VERSION, source_root=Path(__file__).resolve().parent)
APP_LANG = detect_ui_language()
APP_STRINGS = get_app_strings(APP_LANG)
USE_KOREAN: bool = is_korean(APP_LANG)
APP_PATHS = build_app_path_config(
    entry_file=__file__,
    get_runtime_config_value=get_runtime_config_value,
)
# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

APP_UI_CONFIG = build_app_ui_runtime_config(
    get_runtime_config_value=get_runtime_config_value,
    get_bool_env=get_bool_env,
    get_int_env=get_int_env,
)

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_COMPOSITION_CONFIG = build_app_composition_config(
    strings=APP_STRINGS,
    supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
    grid_width=APP_UI_CONFIG.grid_width,
    grid_height=APP_UI_CONFIG.grid_height,
    card_width=APP_UI_CONFIG.card_width,
    card_height=APP_UI_CONFIG.card_height,
    grid_cols=APP_UI_CONFIG.grid_cols,
    grid_rows_visible=APP_UI_CONFIG.grid_rows_visible,
    card_h_spacing=APP_UI_CONFIG.card_h_spacing,
    card_v_spacing=APP_UI_CONFIG.card_v_spacing,
    create_prefixed_logger=get_prefixed_logger,
    gpu_bundle_url=OPTISCALER_GPU_BUNDLE_URL,
    gpu_bundle_debug=OPTISCALER_GPU_BUNDLE_DEBUG,
    game_master_url=OPTISCALER_GAME_MASTER_URL,
    resource_master_url=OPTISCALER_RESOURCE_MASTER_URL,
    message_binding_url=OPTISCALER_MESSAGE_BINDING_URL,
    message_center_url=OPTISCALER_MESSAGE_CENTER_URL,
    max_supported_gpu_count=MAX_SUPPORTED_GPU_COUNT,
    root_width_fallback=APP_UI_CONFIG.window_width,
    root_height_fallback=APP_UI_CONFIG.window_height,
    game_ini_profile_url=OPTISCALER_GAME_INI_PROFILE_URL,
    game_unreal_ini_profile_url=OPTISCALER_GAME_UNREAL_INI_PROFILE_URL,
    engine_ini_profile_url=OPTISCALER_ENGINE_INI_PROFILE_URL,
    game_xml_profile_url=OPTISCALER_GAME_XML_PROFILE_URL,
    registry_profile_url=OPTISCALER_REGISTRY_PROFILE_URL,
    game_json_profile_url=OPTISCALER_GAME_JSON_PROFILE_URL,
)
APP_THEME = APP_COMPOSITION_CONFIG.app_theme
UI_CONTROLLER_FACTORY_CONFIG = APP_COMPOSITION_CONFIG.ui_controller_factory_config
APP_CONTROLLER_FACTORY_CONFIG = APP_COMPOSITION_CONFIG.app_controller_factory_config


class OptiManagerApp:
    def _clear_cards(self) -> None:
        """Clear UI cards (stub implementation)."""
        pass

    def __getattr__(self, name: str):
        return get_runtime_state_attr(self, name)

    def __setattr__(self, name: str, value) -> None:
        if set_runtime_state_attr(self, name, value):
            return
        object.__setattr__(self, name, value)

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.lang = APP_LANG
        self.txt = APP_STRINGS
        configure_app_startup_window(
            self,
            app_version=APP_VERSION,
            app_ui_config=APP_UI_CONFIG,
            logger=logging.getLogger(),
        )
        initialize_app_runtime_state(
            self,
            app_paths=APP_PATHS,
            checking_gpu_text=self.txt.main.checking_gpu,
        )
        initialize_app_infra(
            self,
            app_version=APP_VERSION,
            app_paths=APP_PATHS,
            app_ui_config=APP_UI_CONFIG,
            logger=logging.getLogger(),
        )
        initialize_app_presenters(
            self,
            app_theme=APP_THEME,
            app_ui_config=APP_UI_CONFIG,
            supported_games_wiki_url=SUPPORTED_GAMES_WIKI_URL,
            logger=logging.getLogger(),
        )
        initialize_app_runtime_startup(
            self,
            app_theme=APP_THEME,
            app_ui_config=APP_UI_CONFIG,
            ui_controller_factory_config=UI_CONTROLLER_FACTORY_CONFIG,
            app_controller_factory_config=APP_CONTROLLER_FACTORY_CONFIG,
            logger=logging.getLogger(),
        )

    def _format_gpu_label_text(self, gpu_info: str) -> str:
        normalized_gpu = str(gpu_info or "").strip() or self.txt.main.unknown_gpu
        return self.txt.main.gpu_label_template.format(gpu=normalized_gpu)

    def _set_gpu_label_text(self, text: str) -> None:
        widget = getattr(self, "gpu_lbl", None)
        if widget is None:
            return
        if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
            return
        widget.configure(text=str(text or ""))

    def _set_folder_select_enabled(self, enabled: bool) -> None:
        widget = getattr(self, "btn_select_folder", None)
        if widget is None:
            return
        if hasattr(widget, "winfo_exists") and callable(widget.winfo_exists) and not widget.winfo_exists():
            return
        widget.configure(state="normal" if enabled else "disabled")

    def _is_multi_gpu_block_active(self) -> bool:
        return self.gpu_state.gpu_count > MAX_SUPPORTED_GPU_COUNT

    def _is_vendor_allowed_by_game_flags(self, game_data: dict) -> bool:
        vendor = str(self.sheet_state.active_vendor or "").strip().lower()
        if vendor not in {"intel", "amd", "nvidia"}:
            return True

        support_key = f"support_{vendor}"
        if support_key not in game_data:
            return True

        return parse_support_flag(game_data.get(support_key), native_xefg_means_false=True)

    def _is_game_supported_for_current_gpu(self, game_data: dict) -> bool:
        if not self._is_vendor_allowed_by_game_flags(game_data):
            return False
        if bool(game_data.get("__gpu_bundle_loaded__", False)):
            return bool(game_data.get("__gpu_bundle_supported__", False))
        return gpu_service.matches_gpu_rule(str(game_data.get("supported_gpu", "") or ""), self.gpu_state.gpu_info)

    def _matches_fsr4_skip_rule(self, rule_text: str) -> bool:
        return gpu_service.matches_gpu_rule(APP_PATHS.fsr4_skip_gpu_rule, rule_text)

    def _should_apply_fsr4_for_game(self, game_data: Optional[dict] = None) -> bool:
        if self._matches_fsr4_skip_rule(self.gpu_state.gpu_info):
            return False

        if isinstance(game_data, dict):
            supported_gpu_rule = str(game_data.get("supported_gpu", "") or "").strip()
            if supported_gpu_rule and self._matches_fsr4_skip_rule(supported_gpu_rule):
                return False

        return True

    def _update_install_button_state(self):
        if not hasattr(self, "apply_btn"):
            return

        button_state = compute_install_button_state(self._build_install_button_state_inputs())
        can_install = bool(button_state.enabled)
        is_sheet_loading = button_state.reason_code == "sheet_loading"

        # Cancel any ongoing loading blink
        blink_job = getattr(self, "_loading_blink_job", None)
        if blink_job is not None:
            self.root.after_cancel(blink_job)
            self._loading_blink_job = None

        if button_state.show_installing:
            button_text = self.txt.main.installing_button
        elif can_install:
            button_text = self.txt.main.install_button
        elif is_sheet_loading:
            button_text = self.txt.main.loading_button
        else:
            button_text = ""

        self.apply_btn.configure(
            state="normal" if can_install else "disabled",
            text=button_text,
            fg_color=APP_THEME.install_button_color if can_install else APP_THEME.install_button_disabled_color,
            hover_color=APP_THEME.install_button_hover_color if can_install else APP_THEME.install_button_disabled_color,
            border_color=APP_THEME.install_button_border_color if can_install else APP_THEME.install_button_border_disabled_color,
        )

        # Start blinking if still loading
        if is_sheet_loading:
            self._loading_blink_job = self.root.after(600, self._tick_loading_blink)

    def _tick_loading_blink(self):
        """Toggle install button text to create a blinking loading effect."""
        self._loading_blink_job = None
        if not hasattr(self, "apply_btn"):
            return
        button_state = compute_install_button_state(self._build_install_button_state_inputs())
        if button_state.reason_code != "sheet_loading":
            return
        loading_text = self.txt.main.loading_button
        current_text = self.apply_btn.cget("text")
        self.apply_btn.configure(text="" if current_text == loading_text else loading_text)
        self._loading_blink_job = self.root.after(600, self._tick_loading_blink)

    def _build_install_button_state_inputs(self) -> InstallButtonStateInputs:
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        install_state = self.install_state
        archive_state = self.archive_state
        selection = build_selected_game_snapshot(
            self.found_exe_list,
            self.card_ui_state.selected_game_index,
            getattr(self, "lang", "en"),
        )
        app_update_manager = getattr(self, "_app_update_manager", None)
        return build_install_button_state_inputs(
            selection=selection,
            multi_gpu_blocked=bool(gpu_state.multi_gpu_blocked),
            gpu_selection_pending=bool(gpu_state.gpu_selection_pending),
            sheet_ready=bool(sheet_state.status),
            sheet_loading=bool(sheet_state.loading),
            install_in_progress=bool(install_state.in_progress),
            app_update_in_progress=bool(getattr(app_update_manager, "in_progress", False)),
            install_precheck_running=bool(install_state.precheck_running),
            install_precheck_ok=bool(install_state.precheck_ok),
            optiscaler_archive_ready=bool(archive_state.optiscaler_ready),
            optiscaler_archive_downloading=bool(archive_state.optiscaler_downloading),
            fsr4_archive_ready=bool(archive_state.fsr4_ready),
            fsr4_archive_downloading=bool(archive_state.fsr4_downloading),
            game_popup_confirmed=bool(install_state.popup_confirmed),
            is_game_supported=self._is_game_supported_for_current_gpu,
            should_apply_fsr4=self._should_apply_fsr4_for_game,
        )

    # ------------------------------------------------------------------
    # Async DB load
    # ------------------------------------------------------------------

    def _on_close(self):
        controller = getattr(self, "_app_actions_controller", None)
        if controller is None:
            return
        controller.request_close(bool(self.install_state.in_progress))

    def _shutdown_app(self) -> None:
        controller = getattr(self, "_app_shutdown_controller", None)
        if controller is None:
            return
        controller.shutdown()

    def _start_game_db_load_async(self):
        if getattr(self, "_game_db_controller", None) is None:
            return

        sheet_state = self.sheet_state
        game_db_vendor = str(sheet_state.active_vendor or "default")
        gpu_model = str(getattr(self.gpu_state, "gpu_info", "") or "").strip()
        started = self._game_db_controller.start_load(game_db_vendor, gpu_model)
        if not started:
            return
        logging.info(
            "[APP] Starting Game DB load for vendor=%s gpu=%s",
            game_db_vendor,
            self.gpu_state.gpu_info,
        )

    def _is_scan_in_progress(self) -> bool:
        controller = getattr(self, "_scan_controller", None)
        return bool(controller and controller.is_scan_in_progress)

    def _clear_found_games(self) -> None:
        self.found_exe_list = []

    def _pump_poster_queue(self) -> None:
        self._poster_queue.pump()

    def _start_auto_scan(self):
        """Kick off a silent auto-scan of known launcher/game directories."""
        if self.gpu_state.multi_gpu_blocked:
            return
        if self.install_state.in_progress:
            return
        if getattr(self, "_scan_controller", None) is None:
            return
        self._scan_controller.start_auto_scan()

    def _set_game_folder(self, folder_path: str) -> None:
        self.game_folder = str(folder_path or "")

    def _start_manual_scan_from_folder(self, folder_path: str) -> bool:
        if getattr(self, "_scan_controller", None) is None:
            return False
        if self.install_state.in_progress:
            return False
        return self._scan_controller.start_manual_scan(folder_path)

    # ------------------------------------------------------------------
    # UI builder
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Poster card grid
    # ------------------------------------------------------------------

    def _apply_install_selection_state(self, state: InstallSelectionUiState) -> None:
        install_state = self.install_state
        install_state.popup_confirmed = bool(state.popup_confirmed)
        install_state.precheck_running = bool(state.precheck_running)
        install_state.precheck_ok = bool(state.precheck_ok)
        install_state.precheck_error = str(state.precheck_error or "")
        install_state.precheck_dll_name = str(state.precheck_dll_name or "")

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _build_scan_entry_state(self) -> ScanEntryState:
        gpu_state = self.gpu_state
        sheet_state = self.sheet_state
        return ScanEntryState(
            multi_gpu_blocked=bool(gpu_state.multi_gpu_blocked),
            sheet_loading=bool(sheet_state.loading),
            sheet_ready=bool(sheet_state.status),
        )

    def select_game_folder(self):
        controller = getattr(self, "_scan_entry_controller", None)
        if controller is None:
            return
        if self.install_state.in_progress:
            return
        controller.select_game_folder(self._build_scan_entry_state())

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def apply_optiscaler(self):
        controller = getattr(self, "_install_flow_controller", None)
        if controller is None:
            return
        return controller.apply_selected_install()


if __name__ == "__main__":
    if "--edit-engine-ini" in sys.argv:
        logging.warning("--edit-engine-ini no longer supports Google Sheet source and has been disabled.")
        sys.exit(0)

    request_foreground = has_startup_foreground_request(sys.argv[1:])
    root = ctk.CTk()
    app = OptiManagerApp(root)
    if request_foreground:
        request_window_foreground(root, logger=logging.getLogger("APP"))
    root.mainloop()
