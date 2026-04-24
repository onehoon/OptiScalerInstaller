import logging
import sys
from pathlib import Path

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
from installer.app.path_config import build_app_path_config
from installer.app.runtime_builders import (
    configure_app_startup_window,
    initialize_app_infra,
    initialize_app_presenters,
    initialize_app_runtime_startup,
    initialize_app_runtime_state,
)
from installer.app.runtime_state import (
    get_runtime_state_attr,
    set_runtime_state_attr,
)
from installer.app.ui_runtime_config import (
    build_app_ui_runtime_config,
)
from installer.app.window_focus import has_startup_foreground_request, request_window_foreground
from installer.i18n import (
    detect_ui_language,
    get_app_strings,
)

try:
    import customtkinter as ctk
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "customtkinter is not installed in the current Python environment.\n"
        f"Interpreter: {sys.executable}\n"
        f"Install with: \"{sys.executable}\" -m pip install customtkinter"
    ) from e

try:
    # Import at startup as an explicit dependency check; image loading is used
    # by poster/UI code after the application is initialized.
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
APP_PATHS = build_app_path_config(
    entry_file=__file__,
    get_runtime_config_value=get_runtime_config_value,
)

APP_UI_CONFIG = build_app_ui_runtime_config(
    get_runtime_config_value=get_runtime_config_value,
    get_bool_env=get_bool_env,
    get_int_env=get_int_env,
)

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
        self._app_paths = APP_PATHS
        self._app_theme = APP_THEME
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


if __name__ == "__main__":
    request_foreground = has_startup_foreground_request(sys.argv[1:])
    root = ctk.CTk()
    app = OptiManagerApp(root)
    if request_foreground:
        request_window_foreground(root, logger=logging.getLogger("APP"))
    root.mainloop()
