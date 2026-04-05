from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from .card_grid import compute_visible_game_indices
from .card_factory import create_game_card
from .card_ui import GameCardUiCallbacks, GameCardUiController
from .card_viewport import CardViewportCallbacks, CardViewportController, CardViewportRuntime
from .card_visuals import (
    ensure_game_card_image_cache,
    render_game_card_visual,
    update_game_card_base_image,
)


@dataclass(frozen=True)
class UiControllerFactoryConfig:
    card_width: int
    card_height: int
    grid_cols: int
    grid_rows_visible: int
    card_h_spacing: int
    card_v_spacing: int
    card_background: str
    title_overlay_background: str
    title_overlay_text_color: str
    title_font_family: str
    title_height: int = 34


@dataclass(frozen=True)
class UiControllers:
    card_ui: GameCardUiController
    card_viewport: CardViewportController
    card_viewport_runtime: CardViewportRuntime


def create_card_ui_controller(app: Any, config: UiControllerFactoryConfig) -> GameCardUiController:
    card_items = getattr(app, "card_items", None)
    if card_items is None:
        app.card_items = []
        card_items = app.card_items

    image_refs = getattr(app, "_ctk_images", None)
    if image_refs is None:
        app._ctk_images = []
        image_refs = app._ctk_images

    return GameCardUiController(
        root=getattr(app, "root", None),
        games_scroll=app.games_scroll,
        poster_loader=app._poster_loader,
        poster_queue=app._poster_queue,
        card_ui_state=app.card_ui_state,
        card_items=card_items,
        image_refs=image_refs,
        callbacks=GameCardUiCallbacks(
            get_found_games=lambda: tuple(app.found_exe_list),
            get_grid_column_count=lambda: config.grid_cols,
            get_dynamic_column_count=app._get_dynamic_column_count,
            get_card_render_controller=lambda: getattr(app, "_card_render_controller", None),
            select_game=app._set_selected_game,
            activate_game=app._set_selected_game,
        ),
        card_width=config.card_width,
        card_height=config.card_height,
        card_background=config.card_background,
        title_overlay_background=config.title_overlay_background,
        title_overlay_text_color=config.title_overlay_text_color,
        title_font_family=config.title_font_family,
        title_height=config.title_height,
        grid_rows_visible=config.grid_rows_visible,
        create_game_card_fn=create_game_card,
        ensure_card_image_cache_fn=ensure_game_card_image_cache,
        render_card_visual_fn=render_game_card_visual,
        update_card_base_image_fn=update_game_card_base_image,
        compute_visible_indices_fn=compute_visible_game_indices,
        logger=logging.getLogger(),
    )


def create_card_viewport_bundle(
    app: Any,
    config: UiControllerFactoryConfig,
) -> tuple[CardViewportRuntime, CardViewportController]:
    runtime = CardViewportRuntime(
        grid_cols_current=config.grid_cols,
    )
    controller = CardViewportController(
        root=app.root,
        games_scroll=app.games_scroll,
        poster_queue=app._poster_queue,
        runtime=runtime,
        callbacks=CardViewportCallbacks(
            get_card_frames=lambda: tuple(app.card_frames),
            has_found_games=lambda: bool(app.found_exe_list),
            render_cards=lambda keep_selection: app._render_cards(keep_selection=keep_selection),
            get_effective_widget_scale=app._get_effective_widget_scale,
            publish_runtime_state=lambda: None,
        ),
        card_width=config.card_width,
        card_h_spacing=config.card_h_spacing,
        card_v_spacing=config.card_v_spacing,
        logger=logging.getLogger(),
    )
    return runtime, controller


def build_ui_controllers(app: Any, config: UiControllerFactoryConfig) -> UiControllers:
    card_ui_controller = create_card_ui_controller(app, config)
    card_viewport_runtime, card_viewport_controller = create_card_viewport_bundle(app, config)
    return UiControllers(
        card_ui=card_ui_controller,
        card_viewport=card_viewport_controller,
        card_viewport_runtime=card_viewport_runtime,
    )


def bind_ui_controllers(app: Any, controllers: UiControllers) -> None:
    app._card_ui_controller = controllers.card_ui
    app._card_viewport_runtime = controllers.card_viewport_runtime
    app._card_viewport_controller = controllers.card_viewport


__all__ = [
    "UiControllerFactoryConfig",
    "UiControllers",
    "bind_ui_controllers",
    "build_ui_controllers",
    "create_card_ui_controller",
    "create_card_viewport_bundle",
]
