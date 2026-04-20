from __future__ import annotations

from collections.abc import Callable
import logging
import re
from typing import Any

from .popup_markup import render_markup_to_text_widget, strip_markup_text


VersionNameFormatter = Callable[[str], str]


class HeaderStatusPresenter:
    def __init__(
        self,
        *,
        root: Any,
        status_text_color: str,
        scan_status_text_color: str,
        status_indicator_loading_dim_color: str,
        status_indicator_pulse_ms: int,
        supported_games_wiki_url: str,
        link_active_color: str,
        link_hover_color: str,
        logger=None,
    ) -> None:
        self._root = root
        self._status_text_color = str(status_text_color or "")
        self._scan_status_text_color = str(scan_status_text_color or "")
        self._status_indicator_loading_dim_color = str(status_indicator_loading_dim_color or "")
        self._status_indicator_pulse_ms = max(1, int(status_indicator_pulse_ms))
        self._supported_games_wiki_url = str(supported_games_wiki_url or "").strip()
        self._link_active_color = str(link_active_color or "")
        self._link_hover_color = str(link_hover_color or "")
        self._logger = logger or logging.getLogger()

        self._status_indicator_after_id = None
        self._status_indicator_pulse_visible = True
        self._status_indicator_pulse_colors = ("", "")
        self._status_badge_dot = None

    def shutdown(self) -> None:
        self._stop_status_badge_pulse()

    def set_supported_games_wiki_link_hover(self, label_widget: Any, hovered: bool) -> None:
        if label_widget is None or not label_widget.winfo_exists():
            return
        if not self._supported_games_wiki_url:
            label_widget.configure(text_color=self._status_text_color)
            return
        label_widget.configure(text_color=self._link_hover_color if hovered else self._link_active_color)

    def set_scan_status_message(self, label_widget: Any, text: str = "", text_color: str = "") -> None:
        if label_widget is None or not label_widget.winfo_exists():
            return
        message = str(text or "").strip()
        if not message:
            label_widget.configure(text="")
            label_widget.grid_remove()
            return

        label_widget.configure(text=message, text_color=text_color or self._scan_status_text_color)
        label_widget.grid()

    def set_status_badge_state(
        self,
        *,
        label_widget: Any,
        dot_widget: Any,
        label_text: str,
        indicator_color: str,
        pulse: bool = False,
    ) -> None:
        if label_widget is None or dot_widget is None:
            return
        if not label_widget.winfo_exists() or not dot_widget.winfo_exists():
            return

        self._status_badge_dot = dot_widget
        label_widget.configure(text=label_text, text_color=self._status_text_color)
        if pulse:
            self._start_status_badge_pulse(dot_widget, indicator_color, self._status_indicator_loading_dim_color)
            return

        self._stop_status_badge_pulse()
        dot_widget.configure(fg_color=indicator_color)

    def update_sheet_status(
        self,
        *,
        label_widget: Any,
        dot_widget: Any,
        multi_gpu_blocked: bool,
        gpu_selection_pending: bool,
        sheet_loading: bool,
        sheet_status: bool,
        status_gpu_config_text: str,
        status_gpu_select_text: str,
        status_game_db_text: str,
        indicator_offline: str,
        indicator_warning: str,
        indicator_loading: str,
        indicator_online: str,
    ) -> None:
        if multi_gpu_blocked:
            self.set_status_badge_state(
                label_widget=label_widget,
                dot_widget=dot_widget,
                label_text=status_gpu_config_text,
                indicator_color=indicator_offline,
            )
            return
        if gpu_selection_pending:
            self.set_status_badge_state(
                label_widget=label_widget,
                dot_widget=dot_widget,
                label_text=status_gpu_select_text,
                indicator_color=indicator_warning,
            )
            return
        if sheet_loading:
            self.set_status_badge_state(
                label_widget=label_widget,
                dot_widget=dot_widget,
                label_text=status_game_db_text,
                indicator_color=indicator_loading,
                pulse=True,
            )
            return

        self.set_status_badge_state(
            label_widget=label_widget,
            dot_widget=dot_widget,
            label_text=status_game_db_text,
            indicator_color=indicator_online if sheet_status else indicator_offline,
        )

    def update_selected_game_header(self, label_widget: Any, game_name: str) -> None:
        try:
            if label_widget is not None and label_widget.winfo_exists():
                label_widget.configure(text=str(game_name or ""))
        except Exception:
            self._logger.debug("Failed to update selected game header", exc_info=True)

    def _start_status_badge_pulse(self, dot_widget: Any, active_color: str, dim_color: str) -> None:
        self._stop_status_badge_pulse()
        self._status_badge_dot = dot_widget
        self._status_indicator_pulse_colors = (active_color, dim_color)
        self._status_indicator_pulse_visible = True
        dot_widget.configure(fg_color=active_color)
        self._status_indicator_after_id = self._root.after(
            self._status_indicator_pulse_ms,
            self._tick_status_badge_pulse,
        )

    def _stop_status_badge_pulse(self) -> None:
        try:
            if self._status_indicator_after_id is not None:
                self._root.after_cancel(self._status_indicator_after_id)
        except Exception:
            pass
        self._status_indicator_after_id = None
        self._status_indicator_pulse_visible = True

    def _tick_status_badge_pulse(self) -> None:
        self._status_indicator_after_id = None
        dot_widget = self._status_badge_dot
        if dot_widget is None:
            return
        if not hasattr(dot_widget, "winfo_exists") or not self._root.winfo_exists() or not dot_widget.winfo_exists():
            return

        active_color, dim_color = self._status_indicator_pulse_colors
        next_visible = not self._status_indicator_pulse_visible
        self._status_indicator_pulse_visible = next_visible
        dot_widget.configure(fg_color=active_color if next_visible else dim_color)
        self._status_indicator_after_id = self._root.after(
            self._status_indicator_pulse_ms,
            self._tick_status_badge_pulse,
        )


class BottomPanelPresenter:
    def __init__(
        self,
        *,
        info_text_offset_px: int,
        version_name_formatter: VersionNameFormatter,
        info_emphasis_color: str,
        logger=None,
    ) -> None:
        self._info_text_offset_px = int(info_text_offset_px)
        self._version_name_formatter = version_name_formatter
        self._info_emphasis_color = str(info_emphasis_color or "")
        self._logger = logger or logging.getLogger()

    def refresh_optiscaler_archive_info_ui(
        self,
        version_label: Any,
        *,
        sheet_loading: bool,
        module_download_links: dict[str, Any] | None = None,
        resource_master: dict[str, Any] | None = None,
        version_line_template: str,
    ) -> None:
        if version_label is None:
            return
        if sheet_loading:
            if version_label.winfo_exists():
                version_label.configure(text="")
            return

        links = module_download_links if isinstance(module_download_links, dict) else resource_master
        entry = links.get("optiscaler", {}) if isinstance(links, dict) else {}
        version = ""

        if isinstance(entry, dict):
            raw_display_version = str(entry.get("display_version", "") or entry.get("version", "")).replace("\r", " ").replace("\n", " ").strip()
            version = re.sub(r"\s+", " ", raw_display_version)

        version_display_name = self._version_name_formatter(version)

        if version_display_name:
            version_text = version_line_template.format(value=version_display_name)
        else:
            version_text = version_line_template.format(value="-")

        if version_label.winfo_exists():
            version_label.configure(text=version_text, text_color="#AEB9C8")

    def apply_information_text_shift(self, info_text_widget: Any) -> None:
        try:
            text_widget = getattr(info_text_widget, "_textbox", None)
            if text_widget is None:
                return
            text_widget.configure(spacing1=0, spacing2=0, spacing3=0, pady=0)

            manager = text_widget.winfo_manager()
            if manager == "pack":
                text_widget.pack_configure(pady=(0, self._info_text_offset_px))
            elif manager == "grid":
                text_widget.grid_configure(pady=(0, self._info_text_offset_px))
        except Exception as exc:
            self._logger.debug("Could not adjust information textbox position: %s", exc)

    def set_information_text(self, info_text_widget: Any, *, text: str, no_information_text: str) -> None:
        if info_text_widget is None:
            return

        info_text = (text or "").strip() or no_information_text
        text_widget = getattr(info_text_widget, "_textbox", info_text_widget)
        self.apply_information_text_shift(info_text_widget)
        info_text_widget.configure(state="normal")
        try:
            text_widget.delete("1.0", "end")
            self._insert_information_with_markup(text_widget, info_text)
        except Exception as exc:
            self._logger.warning("Failed to render information markup, falling back to plain text: %s", exc)
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", strip_markup_text(info_text))
        finally:
            info_text_widget.configure(state="disabled")

    def _insert_information_with_markup(self, text_widget: Any, raw_text: str) -> None:
        render_markup_to_text_widget(
            text_widget,
            raw_text,
            emphasis_tag="info_red_emphasis",
            emphasis_color=self._info_emphasis_color,
            emphasis_size_offset=0,
            emphasis_weight="bold",
            trim_emphasis=True,
        )
