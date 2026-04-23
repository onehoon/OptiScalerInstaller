from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .controller_factory import AppControllerFactoryConfig
from .theme import AppThemeBundle, build_app_theme
from .ui_controller_factory import UiControllerFactoryConfig


@dataclass(frozen=True)
class AppCompositionConfigBundle:
    app_theme: AppThemeBundle
    ui_controller_factory_config: UiControllerFactoryConfig
    app_controller_factory_config: AppControllerFactoryConfig


def build_app_composition_config(
    *,
    strings: Any,
    supported_games_wiki_url: str,
    grid_width: int,
    grid_height: int,
    card_width: int,
    card_height: int,
    grid_cols: int,
    grid_rows_visible: int,
    card_h_spacing: int,
    card_v_spacing: int,
    create_prefixed_logger: Callable[[str], Any],
    gpu_bundle_url: str,
    gpu_bundle_debug: bool,
    game_master_url: str,
    resource_master_url: str,
    message_binding_url: str,
    message_center_url: str,
    max_supported_gpu_count: int,
    root_width_fallback: int,
    root_height_fallback: int,
    game_ini_profile_url: str,
    game_unreal_ini_profile_url: str,
    engine_ini_profile_url: str,
    game_xml_profile_url: str,
    registry_profile_url: str,
    game_json_profile_url: str,
) -> AppCompositionConfigBundle:
    app_theme = build_app_theme(
        strings,
        supported_games_wiki_url=supported_games_wiki_url,
        grid_width=grid_width,
        grid_height=grid_height,
    )
    ui_controller_factory_config = UiControllerFactoryConfig(
        card_width=card_width,
        card_height=card_height,
        grid_cols=grid_cols,
        grid_rows_visible=grid_rows_visible,
        card_h_spacing=card_h_spacing,
        card_v_spacing=card_v_spacing,
        card_background=app_theme.card_background,
        title_overlay_background=app_theme.card_title_overlay_background,
        title_overlay_text_color=app_theme.card_title_overlay_text,
        title_font_family=app_theme.font_ui,
        title_height=34,
    )
    app_controller_factory_config = AppControllerFactoryConfig(
        create_prefixed_logger=create_prefixed_logger,
        gpu_bundle_url=gpu_bundle_url,
        gpu_bundle_debug=gpu_bundle_debug,
        game_master_url=game_master_url,
        resource_master_url=resource_master_url,
        message_binding_url=message_binding_url,
        message_center_url=message_center_url,
        gpu_notice_theme=app_theme.gpu_notice_theme,
        max_supported_gpu_count=max_supported_gpu_count,
        message_popup_theme=app_theme.message_popup_theme,
        root_width_fallback=root_width_fallback,
        root_height_fallback=root_height_fallback,
        supported_games_wiki_url=supported_games_wiki_url,
        game_ini_profile_url=game_ini_profile_url,
        game_unreal_ini_profile_url=game_unreal_ini_profile_url,
        engine_ini_profile_url=engine_ini_profile_url,
        game_xml_profile_url=game_xml_profile_url,
        registry_profile_url=registry_profile_url,
        game_json_profile_url=game_json_profile_url,
    )
    return AppCompositionConfigBundle(
        app_theme=app_theme,
        ui_controller_factory_config=ui_controller_factory_config,
        app_controller_factory_config=app_controller_factory_config,
    )


__all__ = [
    "AppCompositionConfigBundle",
    "build_app_composition_config",
]
