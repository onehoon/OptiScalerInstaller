from __future__ import annotations

from typing import Any

from .ui_shell import AppUiShell


def get_app_ui_shell(app: Any) -> AppUiShell | None:
    return getattr(app, "_ui_shell", None)


def set_supported_games_wiki_link_hover(app: Any, hovered: bool) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.set_supported_games_wiki_link_hover(hovered)


def open_supported_games_wiki(app: Any, _event=None) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.open_supported_games_wiki()


def set_scan_status_message(app: Any, text: str = "", text_color: str | None = None) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.set_scan_status_message(text, text_color)


def set_status_badge_state(
    app: Any,
    label_text: str,
    indicator_color: str,
    pulse: bool = False,
) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.set_status_badge_state(label_text=label_text, indicator_color=indicator_color, pulse=pulse)


def update_selected_game_header(app: Any) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.update_selected_game_header()


def refresh_optiscaler_archive_info_ui(app: Any) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.refresh_optiscaler_archive_info_ui(
        sheet_loading=bool(app.sheet_state.loading),
        module_download_links=app.sheet_state.module_download_links,
    )


def apply_information_text_shift(app: Any) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.apply_information_text_shift()


def update_sheet_status(app: Any) -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    gpu_state = app.gpu_state
    sheet_state = app.sheet_state
    shell.update_sheet_status(
        multi_gpu_blocked=gpu_state.multi_gpu_blocked,
        gpu_selection_pending=gpu_state.gpu_selection_pending,
        sheet_loading=sheet_state.loading,
        sheet_status=sheet_state.status,
    )


def set_information_text(app: Any, text: str = "") -> None:
    shell = get_app_ui_shell(app)
    if shell is None:
        return
    shell.set_information_text(text=text)


__all__ = [
    "apply_information_text_shift",
    "get_app_ui_shell",
    "open_supported_games_wiki",
    "refresh_optiscaler_archive_info_ui",
    "set_information_text",
    "set_scan_status_message",
    "set_status_badge_state",
    "set_supported_games_wiki_link_hover",
    "update_selected_game_header",
    "update_sheet_status",
]
