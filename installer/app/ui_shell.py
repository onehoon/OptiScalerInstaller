from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from typing import Any

from installer.data.game_db_keys import GUIDE_URL_KEY, INSTALLED_PROXY_NAME_KEY
from installer.i18n import pick_bound_message

from .install_state import build_selected_game_snapshot
from .notice_controller import AppNoticeController
from .ui_presenters import BottomPanelPresenter, HeaderStatusPresenter


@dataclass(frozen=True)
class AppUiShellCallbacks:
    get_found_games: Callable[[], Sequence[Mapping[str, Any]]]
    get_selected_game_index: Callable[[], int | None]
    get_lang: Callable[[], str]


class AppUiShell:
    def __init__(
        self,
        *,
        txt: Any,
        get_notice_controller: Callable[[], AppNoticeController | None],
        get_header_presenter: Callable[[], HeaderStatusPresenter | None],
        get_bottom_presenter: Callable[[], BottomPanelPresenter | None],
        callbacks: AppUiShellCallbacks,
        get_lbl_scan_status: Callable[[], Any],
        get_status_badge_label: Callable[[], Any],
        get_status_badge_dot: Callable[[], Any],
        get_lbl_selected_game_header: Callable[[], Any],
        get_lbl_optiscaler_version_line: Callable[[], Any],
        get_info_text: Callable[[], Any],
        get_lbl_supported_games_wiki_link: Callable[[], Any],
        scan_status_text_color: str,
        status_indicator_offline_color: str,
        status_indicator_warning_color: str,
        status_indicator_loading_color: str,
        status_indicator_online_color: str,
        logger=None,
    ) -> None:
        self._txt = txt
        self._get_notice_controller = get_notice_controller
        self._get_header_presenter = get_header_presenter
        self._get_bottom_presenter = get_bottom_presenter
        self._callbacks = callbacks
        self._get_lbl_scan_status = get_lbl_scan_status
        self._get_status_badge_label = get_status_badge_label
        self._get_status_badge_dot = get_status_badge_dot
        self._get_lbl_selected_game_header = get_lbl_selected_game_header
        self._get_lbl_optiscaler_version_line = get_lbl_optiscaler_version_line
        self._get_info_text = get_info_text
        self._get_lbl_supported_games_wiki_link = get_lbl_supported_games_wiki_link
        self._scan_status_text_color = scan_status_text_color
        self._status_indicator_offline_color = status_indicator_offline_color
        self._status_indicator_warning_color = status_indicator_warning_color
        self._status_indicator_loading_color = status_indicator_loading_color
        self._status_indicator_online_color = status_indicator_online_color
        self._logger = logger or logging.getLogger()

    def show_precheck_popup(
        self,
        message_text: str,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        controller = self._get_notice_controller()
        if controller is None:
            return
        controller.show_precheck_popup(message_text, on_close=on_close)

    def set_supported_games_wiki_link_hover(self, hovered: bool) -> None:
        label_widget = self._get_lbl_supported_games_wiki_link()
        presenter = self._get_header_presenter()
        if presenter is None:
            return
        presenter.set_supported_games_wiki_link_hover(label_widget, hovered)

    def open_supported_games_wiki(self) -> bool:
        controller = self._get_notice_controller()
        if controller is None:
            return False
        return bool(controller.open_supported_games_wiki())

    def set_scan_status_message(self, text: str = "", text_color: str | None = None) -> None:
        label_widget = self._get_lbl_scan_status()
        presenter = self._get_header_presenter()
        if presenter is None:
            return
        presenter.set_scan_status_message(
            label_widget,
            text,
            text_color or self._scan_status_text_color,
        )

    def set_status_badge_state(
        self,
        label_text: str,
        indicator_color: str,
        pulse: bool = False,
    ) -> None:
        label_widget = self._get_status_badge_label()
        dot_widget = self._get_status_badge_dot()
        presenter = self._get_header_presenter()
        if presenter is None:
            return
        presenter.set_status_badge_state(
            label_widget=label_widget,
            dot_widget=dot_widget,
            label_text=label_text,
            indicator_color=indicator_color,
            pulse=bool(pulse),
        )

    def get_selected_game_header_text(self) -> str:
        selection = build_selected_game_snapshot(
            self._callbacks.get_found_games(),
            self._callbacks.get_selected_game_index(),
            self._callbacks.get_lang(),
        )
        return selection.header_text

    def update_selected_game_header(self) -> None:
        label_widget = self._get_lbl_selected_game_header()
        presenter = self._get_header_presenter()
        if presenter is None:
            return
        presenter.update_selected_game_header(label_widget, self.get_selected_game_header_text())

    def show_after_install_popup(self, game: Mapping[str, Any]) -> None:
        controller = self._get_notice_controller()
        if controller is None:
            return
        normalized_game = dict(game or {})
        installed_file_name = str(normalized_game.get(INSTALLED_PROXY_NAME_KEY) or "").strip()
        completion_template = str(
            getattr(getattr(self._txt, "dialogs", None), "installation_completed_with_name_template", "") or ""
        ).strip()
        completion_message = (
            completion_template.format(name=installed_file_name)
            if completion_template and installed_file_name
            else ""
        )
        game_message = pick_bound_message(normalized_game, "install_post", self._callbacks.get_lang())
        combined_message = "[P]".join(part for part in (completion_message, game_message) if part)
        controller.show_after_install_popup(
            combined_message,
            guide_url=str(normalized_game.get(GUIDE_URL_KEY) or ""),
            guide_context=str(normalized_game.get("display", "<unknown>") or "<unknown>"),
        )

    def refresh_optiscaler_archive_info_ui(
        self,
        *,
        sheet_loading: bool,
        module_download_links: dict[str, Any],
    ) -> None:
        version_label = self._get_lbl_optiscaler_version_line()
        presenter = self._get_bottom_presenter()
        if presenter is None:
            return
        presenter.refresh_optiscaler_archive_info_ui(
            version_label,
            sheet_loading=bool(sheet_loading),
            module_download_links=module_download_links,
            version_line_template=self._txt.main.version_line_template,
        )

    def apply_information_text_shift(self) -> None:
        info_text_widget = self._get_info_text()
        presenter = self._get_bottom_presenter()
        if presenter is None:
            return
        presenter.apply_information_text_shift(info_text_widget)

    def update_sheet_status(
        self,
        *,
        multi_gpu_blocked: bool,
        gpu_selection_pending: bool,
        sheet_loading: bool,
        sheet_status: bool,
    ) -> None:
        label_widget = self._get_status_badge_label()
        dot_widget = self._get_status_badge_dot()
        presenter = self._get_header_presenter()
        if presenter is None:
            return
        presenter.update_sheet_status(
            label_widget=label_widget,
            dot_widget=dot_widget,
            multi_gpu_blocked=bool(multi_gpu_blocked),
            gpu_selection_pending=bool(gpu_selection_pending),
            sheet_loading=bool(sheet_loading),
            sheet_status=bool(sheet_status),
            status_gpu_config_text=self._txt.main.status_gpu_config,
            status_gpu_select_text=self._txt.main.status_gpu_select,
            status_game_db_text=self._txt.main.status_game_db,
            indicator_offline=self._status_indicator_offline_color,
            indicator_warning=self._status_indicator_warning_color,
            indicator_loading=self._status_indicator_loading_color,
            indicator_online=self._status_indicator_online_color,
        )

    def set_information_text(self, *, text: str = "") -> None:
        info_text_widget = self._get_info_text()
        presenter = self._get_bottom_presenter()
        if presenter is None:
            return
        presenter.set_information_text(
            info_text_widget,
            text=text,
            no_information_text=self._txt.main.no_information,
        )


def create_ui_shell(
    app: Any,
    *,
    scan_status_text_color: str,
    status_indicator_offline_color: str,
    status_indicator_warning_color: str,
    status_indicator_loading_color: str,
    status_indicator_online_color: str,
) -> AppUiShell:
    return AppUiShell(
        txt=app.txt,
        get_notice_controller=lambda: getattr(app, "_app_notice_controller", None),
        get_header_presenter=lambda: getattr(app, "_header_status_presenter", None),
        get_bottom_presenter=lambda: getattr(app, "_bottom_panel_presenter", None),
        callbacks=AppUiShellCallbacks(
            get_found_games=lambda: tuple(getattr(app, "found_exe_list", ())),
            get_selected_game_index=lambda: getattr(getattr(app, "card_ui_state", None), "selected_game_index", None),
            get_lang=lambda: str(getattr(app, "lang", "en") or "en"),
        ),
        get_lbl_scan_status=lambda: getattr(app, "lbl_scan_status", None),
        get_status_badge_label=lambda: getattr(app, "status_badge_label", None),
        get_status_badge_dot=lambda: getattr(app, "status_badge_dot", None),
        get_lbl_selected_game_header=lambda: getattr(app, "lbl_selected_game_header", None),
        get_lbl_optiscaler_version_line=lambda: getattr(app, "lbl_optiscaler_version_line", None),
        get_info_text=lambda: getattr(app, "info_text", None),
        get_lbl_supported_games_wiki_link=lambda: getattr(app, "lbl_supported_games_wiki_link", None),
        scan_status_text_color=scan_status_text_color,
        status_indicator_offline_color=status_indicator_offline_color,
        status_indicator_warning_color=status_indicator_warning_color,
        status_indicator_loading_color=status_indicator_loading_color,
        status_indicator_online_color=status_indicator_online_color,
        logger=logging.getLogger(),
    )


__all__ = [
    "AppUiShell",
    "AppUiShellCallbacks",
    "create_ui_shell",
]
