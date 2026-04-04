from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .card_grid import CardGridPlacement, get_card_grid_placement


@dataclass(frozen=True)
class CardRenderCallbacks:
    append_found_game: Callable[[Mapping[str, Any]], int]
    clear_cards: Callable[[bool], None]
    hide_empty_label: Callable[[], None]
    configure_card_columns: Callable[[int], None]
    create_and_place_card: Callable[[int, Mapping[str, Any], CardGridPlacement], None]
    fit_cards_to_visible_width: Callable[[int], None]
    restore_selection: Callable[[int, Mapping[str, Any]], None]
    schedule_scrollregion_refresh: Callable[[], None]
    pump_poster_queue: Callable[[], None]


class CardRenderController:
    def __init__(self, *, callbacks: CardRenderCallbacks) -> None:
        self._callbacks = callbacks

    def render_cards(
        self,
        found_games: Sequence[Mapping[str, Any]],
        *,
        cols: int,
        keep_selection: bool,
        previous_selected_index: int | None,
    ) -> None:
        normalized_games = tuple(found_games or ())
        normalized_cols = max(1, int(cols))

        self._callbacks.clear_cards(bool(keep_selection))
        self._callbacks.hide_empty_label()
        self._callbacks.configure_card_columns(normalized_cols)

        for index, game in enumerate(normalized_games):
            placement = get_card_grid_placement(index, normalized_cols)
            self._callbacks.create_and_place_card(index, game, placement)

        self._callbacks.fit_cards_to_visible_width(normalized_cols)

        if (
            keep_selection
            and previous_selected_index is not None
            and 0 <= int(previous_selected_index) < len(normalized_games)
        ):
            selected_index = int(previous_selected_index)
            self._callbacks.restore_selection(selected_index, normalized_games[selected_index])

        if not normalized_games:
            self._callbacks.hide_empty_label()

        self._callbacks.schedule_scrollregion_refresh()
        self._callbacks.pump_poster_queue()

    def add_game_card(
        self,
        game: Mapping[str, Any],
        *,
        cols: int,
        target_cols: int,
    ) -> None:
        normalized_cols = max(1, int(cols))
        normalized_target_cols = max(1, int(target_cols))
        index = self._callbacks.append_found_game(game)
        placement = get_card_grid_placement(index, normalized_cols)
        self._callbacks.create_and_place_card(index, game, placement)

        if normalized_target_cols < normalized_cols:
            self._callbacks.fit_cards_to_visible_width(normalized_target_cols)

        self._callbacks.schedule_scrollregion_refresh()


__all__ = [
    "CardRenderCallbacks",
    "CardRenderController",
]
