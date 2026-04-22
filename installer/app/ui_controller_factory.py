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
from .runtime_state import CardUiRuntimeState


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


@dataclass(frozen=True)
class UiControllerFactoryDeps:
    """Explicit UI-factory inputs assembled by OptiManagerApp."""

    root: Any
    games_scroll: Any
    poster_loader: Any
    poster_queue: Any
    card_ui_state: CardUiRuntimeState
    card_items: list[dict[str, Any]]
    image_refs: list[Any]
    card_ui_callbacks: GameCardUiCallbacks
    card_viewport_callbacks: CardViewportCallbacks


def create_card_ui_controller(deps: UiControllerFactoryDeps, config: UiControllerFactoryConfig) -> GameCardUiController:
    return GameCardUiController(
        root=deps.root,
        games_scroll=deps.games_scroll,
        poster_loader=deps.poster_loader,
        poster_queue=deps.poster_queue,
        card_ui_state=deps.card_ui_state,
        card_items=deps.card_items,
        image_refs=deps.image_refs,
        callbacks=deps.card_ui_callbacks,
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
    deps: UiControllerFactoryDeps,
    config: UiControllerFactoryConfig,
) -> tuple[CardViewportRuntime, CardViewportController]:
    runtime = CardViewportRuntime(
        grid_cols_current=config.grid_cols,
    )
    controller = CardViewportController(
        root=deps.root,
        games_scroll=deps.games_scroll,
        poster_queue=deps.poster_queue,
        runtime=runtime,
        callbacks=deps.card_viewport_callbacks,
        card_width=config.card_width,
        card_h_spacing=config.card_h_spacing,
        card_v_spacing=config.card_v_spacing,
        logger=logging.getLogger(),
    )
    return runtime, controller


def build_ui_controllers(deps: UiControllerFactoryDeps, config: UiControllerFactoryConfig) -> UiControllers:
    card_ui_controller = create_card_ui_controller(deps, config)
    card_viewport_runtime, card_viewport_controller = create_card_viewport_bundle(deps, config)
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
    "UiControllerFactoryDeps",
    "UiControllers",
    "bind_ui_controllers",
    "build_ui_controllers",
    "create_card_ui_controller",
    "create_card_viewport_bundle",
]
