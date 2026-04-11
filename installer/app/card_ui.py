from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from typing import Any

from .card_factory import GameCardTheme, create_game_card
from .card_grid import compute_visible_game_indices
from .card_visuals import (
    GameCardVisualTheme,
    ensure_game_card_image_cache,
    render_game_card_visual,
    update_game_card_base_image,
)
from .runtime_state import CardUiRuntimeState


@dataclass(frozen=True)
class GameCardUiCallbacks:
    get_found_games: Callable[[], Sequence[Mapping[str, Any]]]
    get_grid_column_count: Callable[[], int]
    get_dynamic_column_count: Callable[[], int]
    get_card_render_controller: Callable[[], Any | None]
    select_game: Callable[[int], None]
    activate_game: Callable[[int], None]


class GameCardUiController:
    def __init__(
        self,
        *,
        root: Any,
        games_scroll: Any,
        poster_loader: Any,
        poster_queue: Any,
        card_ui_state: CardUiRuntimeState,
        card_items: list[dict[str, Any]],
        image_refs: list[Any],
        callbacks: GameCardUiCallbacks,
        card_width: int,
        card_height: int,
        card_background: str,
        title_overlay_background: str,
        title_overlay_text_color: str,
        title_font_family: str,
        title_height: int = 34,
        grid_rows_visible: int = 4,
        create_game_card_fn: Callable[..., Any] = create_game_card,
        ensure_card_image_cache_fn: Callable[..., None] = ensure_game_card_image_cache,
        render_card_visual_fn: Callable[..., None] = render_game_card_visual,
        update_card_base_image_fn: Callable[..., bool] = update_game_card_base_image,
        compute_visible_indices_fn: Callable[..., set[int]] = compute_visible_game_indices,
        logger=None,
    ) -> None:
        self._root = root
        self._games_scroll = games_scroll
        self._poster_loader = poster_loader
        self._poster_queue = poster_queue
        self._card_ui_state = card_ui_state
        self._card_items = card_items
        self._image_refs = image_refs
        self._callbacks = callbacks
        self._card_width = int(card_width)
        self._card_height = int(card_height)
        self._card_background = str(card_background)
        self._title_overlay_background = str(title_overlay_background)
        self._title_overlay_text_color = str(title_overlay_text_color)
        self._title_font_family = str(title_font_family)
        self._title_height = int(title_height)
        self._grid_rows_visible = max(1, int(grid_rows_visible))
        self._create_game_card = create_game_card_fn
        self._ensure_card_image_cache = ensure_card_image_cache_fn
        self._render_card_visual = render_card_visual_fn
        self._update_card_base_image = update_card_base_image_fn
        self._compute_visible_indices = compute_visible_indices_fn
        self._logger = logger or logging.getLogger()

    def build_card_visual_theme(self) -> GameCardVisualTheme:
        return GameCardVisualTheme(
            card_background=self._card_background,
            card_width=self._card_width,
            card_height=self._card_height,
            title_overlay_y=self._card_height - self._title_height,
        )

    def ensure_card_image_cache(self, item: dict[str, Any]) -> None:
        self._ensure_card_image_cache(
            item,
            theme=self.build_card_visual_theme(),
            image_refs=self._image_refs,
        )

    def refresh_card_visual(self, index: int) -> None:
        if index < 0 or index >= len(self._card_items):
            return

        item = self._card_items[index]
        self._render_card_visual(
            item,
            selected=self._card_ui_state.selected_game_index == index,
            hovered=self._card_ui_state.hovered_card_index == index,
            theme=self.build_card_visual_theme(),
            image_refs=self._image_refs,
        )

    def refresh_all_card_visuals(self) -> None:
        for index in range(len(self._card_items)):
            self.refresh_card_visual(index)

    def set_card_image_updates_suspended(self, suspended: bool) -> None:
        self._card_ui_state.image_updates_suspended = bool(suspended)
        if not suspended:
            self._flush_deferred_image_updates()

    def set_card_base_image(self, index: int, label: Any, pil_img: Any) -> None:
        if index < 0 or index >= len(self._card_items):
            return

        item = self._card_items[index]
        if not self._update_card_base_image(
            item,
            label=label,
            pil_img=pil_img,
        ):
            return
        if self._card_ui_state.image_updates_suspended:
            self._card_ui_state.deferred_image_update_indices.add(int(index))
            return
        self.refresh_card_visual(index)

    def handle_card_hover_enter(self, index: int) -> None:
        previous_index = self._card_ui_state.hovered_card_index
        self._card_ui_state.hovered_card_index = int(index)
        if previous_index is not None and previous_index != index:
            self.refresh_card_visual(previous_index)
        self.refresh_card_visual(index)

    def handle_card_hover_leave(self, index: int) -> None:
        if self._card_ui_state.hovered_card_index == index:
            self._card_ui_state.hovered_card_index = None
        self.refresh_card_visual(index)

    def render_cards(self, keep_selection: bool = False) -> None:
        controller = self._callbacks.get_card_render_controller()
        if controller is None:
            return

        self._card_ui_state.deferred_image_update_indices.clear()
        controller.render_cards(
            tuple(self._callbacks.get_found_games() or ()),
            cols=max(1, int(self._callbacks.get_dynamic_column_count() or 1)),
            keep_selection=bool(keep_selection),
            previous_selected_index=self._card_ui_state.selected_game_index if keep_selection else None,
        )

    def make_card(self, index: int, game: Mapping[str, Any]) -> Any:
        result = self._create_game_card(
            parent=self._games_scroll,
            index=index,
            game=game,
            theme=GameCardTheme(
                card_width=self._card_width,
                card_height=self._card_height,
                card_background=self._card_background,
                title_overlay_background=self._title_overlay_background,
                title_overlay_text_color=self._title_overlay_text_color,
                title_font_family=self._title_font_family,
                title_wrap_width=self._card_width - 10,
                title_height=self._title_height,
            ),
            make_placeholder_image=self._poster_loader.make_placeholder_image,
            on_select=self._callbacks.select_game,
            on_activate=self._callbacks.activate_game,
            on_hover_enter=self.handle_card_hover_enter,
            on_hover_leave=self.handle_card_hover_leave,
            set_card_placeholder=self.set_card_placeholder,
            queue_poster=self._queue_poster,
        )
        self._card_items.append(result.card_item)
        self.refresh_card_visual(index)
        return result.card

    def set_card_placeholder(self, index: int, label: Any, _title: str) -> None:
        placeholder_factory = getattr(self._poster_loader, "make_placeholder_image", None)
        if not callable(placeholder_factory):
            return

        # The card item is initialized with a placeholder image and rendered
        # immediately after it is appended. Avoid scheduling a delayed
        # placeholder update here because it can race with a cached poster load
        # and overwrite the real cover after it has already been applied.
        if index < 0 or index >= len(self._card_items):
            return

        pil_img = placeholder_factory()
        self.set_card_base_image(index, label, pil_img)

    def visible_game_indices(self) -> set[int]:
        total = len(tuple(self._callbacks.get_found_games() or ()))
        cols = max(1, int(self._callbacks.get_grid_column_count() or 1))
        yview_start = None
        yview_end = None

        try:
            canvas = getattr(self._games_scroll, "_parent_canvas", None)
            if canvas is not None:
                yview_start, yview_end = canvas.yview()
        except Exception:
            self._logger.debug("Failed to read games canvas yview", exc_info=True)

        return self._compute_visible_indices(
            total,
            cols,
            visible_row_count=self._grid_rows_visible,
            yview_start=yview_start,
            yview_end=yview_end,
        )

    def _queue_poster(
        self,
        index: int,
        label: Any,
        title: str,
        filename_cover: str,
        cover_url: str,
    ) -> None:
        queue_method = getattr(self._poster_queue, "queue", None)
        if not callable(queue_method):
            return
        queue_method(index, label, title, filename_cover, cover_url)

    def _flush_deferred_image_updates(self) -> None:
        pending_indices = sorted(self._card_ui_state.deferred_image_update_indices)
        self._card_ui_state.deferred_image_update_indices.clear()
        for index in pending_indices:
            self.refresh_card_visual(index)


__all__ = [
    "GameCardUiCallbacks",
    "GameCardUiController",
]
