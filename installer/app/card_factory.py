from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import customtkinter as ctk


CardIndexCallback = Callable[[int], None]
CardPlaceholderCallback = Callable[[int, Any, str], None]
PosterQueueCallback = Callable[[int, Any, str, str, str], None]
PlaceholderImageFactory = Callable[[], Any]


@dataclass(frozen=True)
class GameCardTheme:
    card_width: int
    card_height: int
    card_background: str
    title_overlay_background: str
    title_overlay_text_color: str
    title_font_family: str
    title_font_size: int = 11
    title_wrap_width: int = 0
    title_height: int = 34


@dataclass(frozen=True)
class GameCardBuildResult:
    card: Any
    card_item: dict[str, Any]


def create_game_card(
    *,
    parent: Any,
    index: int,
    game: Mapping[str, Any],
    theme: GameCardTheme,
    make_placeholder_image: PlaceholderImageFactory,
    on_select: CardIndexCallback,
    on_activate: CardIndexCallback,
    on_hover_enter: CardIndexCallback,
    on_hover_leave: CardIndexCallback,
    set_card_placeholder: CardPlaceholderCallback,
    queue_poster: PosterQueueCallback,
) -> GameCardBuildResult:
    display_name = str(game["display"])
    card = ctk.CTkFrame(
        parent,
        width=int(theme.card_width),
        fg_color=theme.card_background,
        corner_radius=0,
        border_width=2,
        border_color=theme.card_background,
    )
    card.grid_propagate(False)
    card.configure(height=int(theme.card_height))

    img_label = ctk.CTkLabel(card, text="", width=int(theme.card_width), height=int(theme.card_height))
    img_label.grid(row=0, column=0, padx=0, pady=0)

    hover_title = ctk.CTkLabel(
        card,
        text=display_name,
        font=ctk.CTkFont(family=theme.title_font_family, size=int(theme.title_font_size), weight="bold"),
        text_color=theme.title_overlay_text_color,
        fg_color=theme.title_overlay_background,
        corner_radius=0,
        wraplength=int(theme.title_wrap_width),
        justify="center",
        width=int(theme.card_width),
        height=int(theme.title_height),
    )
    hover_title.place_forget()

    def _handle_select(_event=None, idx=index) -> None:
        on_select(idx)

    def _handle_activate(_event=None, idx=index) -> None:
        on_activate(idx)

    def _handle_hover_enter(_event=None, idx=index) -> None:
        on_hover_enter(idx)

    def _handle_hover_leave(_event=None, idx=index) -> None:
        on_hover_leave(idx)

    for widget in (card, img_label, hover_title):
        widget.bind("<Button-1>", _handle_select)
        widget.bind("<Double-Button-1>", _handle_activate)
        widget.bind("<Enter>", _handle_hover_enter)
        widget.bind("<Leave>", _handle_hover_leave)

    set_card_placeholder(index, img_label, display_name)
    queue_poster(
        index,
        img_label,
        display_name,
        str(game.get("filename_cover", "") or ""),
        str(game.get("cover_url", "") or ""),
    )

    return GameCardBuildResult(
        card=card,
        card_item={
            "card": card,
            "img_label": img_label,
            "hover_title": hover_title,
            "base_pil": make_placeholder_image(),
            "base_revision": 0,
            "ctk_img_cache": {},
            "ctk_img_cache_revision": -1,
            "current_image_state": None,
            "is_default_poster": True,
        },
    )


__all__ = [
    "GameCardBuildResult",
    "GameCardTheme",
    "create_game_card",
]
