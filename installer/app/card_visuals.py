from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

import customtkinter as ctk


@dataclass(frozen=True)
class GameCardVisualTheme:
    card_background: str
    card_width: int
    card_height: int
    title_overlay_y: int


def ensure_game_card_image_cache(
    item: MutableMapping[str, Any],
    *,
    theme: GameCardVisualTheme,
    image_refs: list[Any],
) -> None:
    base_revision = int(item.get("base_revision", 0))
    if item.get("ctk_img_cache_revision") == base_revision and item.get("ctk_img_cache"):
        return

    base_pil = item["base_pil"]
    normal_img = base_pil.convert("RGBA")
    ctk_cache = {
        "normal": ctk.CTkImage(
            light_image=normal_img,
            dark_image=normal_img,
            size=(int(theme.card_width), int(theme.card_height)),
        ),
    }
    image_refs.extend(ctk_cache.values())
    item["ctk_img_cache"] = ctk_cache
    item["ctk_img_cache_revision"] = base_revision
    item["current_image_state"] = None


def render_game_card_visual(
    item: MutableMapping[str, Any],
    *,
    selected: bool,
    hovered: bool,
    theme: GameCardVisualTheme,
    image_refs: list[Any],
) -> None:
    title_overlay = item["hover_title"]
    item["card"].configure(
        border_color=theme.card_background,
        fg_color=theme.card_background,
        border_width=2,
    )

    if selected or hovered:
        title_overlay.place(x=0, y=int(theme.title_overlay_y))
        title_overlay.lift()
    else:
        title_overlay.place_forget()

    ensure_game_card_image_cache(
        item,
        theme=theme,
        image_refs=image_refs,
    )
    if item.get("current_image_state") == "normal":
        return

    item["img_label"].configure(image=item["ctk_img_cache"]["normal"])
    item["current_image_state"] = "normal"


def update_game_card_base_image(
    item: MutableMapping[str, Any],
    *,
    label: Any,
    pil_img: Any,
) -> bool:
    if item.get("img_label") is not label:
        return False

    item["base_pil"] = pil_img.convert("RGBA")
    item["base_revision"] = int(item.get("base_revision", 0)) + 1
    item["ctk_img_cache"] = {}
    item["ctk_img_cache_revision"] = -1
    item["current_image_state"] = None
    return True


__all__ = [
    "GameCardVisualTheme",
    "ensure_game_card_image_cache",
    "render_game_card_visual",
    "update_game_card_base_image",
]
