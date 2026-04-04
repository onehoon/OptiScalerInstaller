from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import rtss_notice
from .install_state import build_selected_game_snapshot


@dataclass(frozen=True)
class AppActionCallbacks:
    show_close_while_installing_warning: Callable[[], None]
    perform_shutdown: Callable[[], None]
    check_for_update: Callable[[Mapping[str, object], bool], bool]
    create_prefixed_logger: Callable[[str], Any]


class AppActionsController:
    def __init__(
        self,
        *,
        root,
        callbacks: AppActionCallbacks,
        use_korean: bool,
        assets_dir: str | Path,
        rtss_theme: rtss_notice.RtssNoticeTheme,
    ) -> None:
        self._root = root
        self._callbacks = callbacks
        self._use_korean = bool(use_korean)
        self._assets_dir = Path(assets_dir)
        self._rtss_theme = rtss_theme

    def request_close(self, install_in_progress: bool) -> bool:
        if install_in_progress:
            self._callbacks.show_close_while_installing_warning()
            return False

        self._callbacks.perform_shutdown()
        return True

    def check_app_update(self, module_download_links: Mapping[str, object], *, blocked: bool) -> bool:
        return bool(self._callbacks.check_for_update(module_download_links, bool(blocked)))

    def show_rtss_notice(
        self,
        found_games: Sequence[Mapping[str, Any]],
        selected_game_index: int | None,
        lang: str,
        module_download_links: Mapping[str, object],
    ) -> None:
        selection = build_selected_game_snapshot(found_games, selected_game_index, lang)
        logger = None
        if selection.selected_game is not None:
            logger = self._callbacks.create_prefixed_logger(selection.selected_game.get("game_name", "unknown"))

        rtss_notice.check_and_show_rtss_notice(
            root=self._root,
            module_download_links=module_download_links,
            use_korean=self._use_korean,
            assets_dir=self._assets_dir,
            theme=self._rtss_theme,
            logger=logger,
        )


__all__ = [
    "AppActionCallbacks",
    "AppActionsController",
]
