from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable


PopupShowCallback = Callable[..., Any]
BlockedStateProvider = Callable[[], bool]
WarningTextProvider = Callable[[], str]


@dataclass(frozen=True)
class StartupFlowCallbacks:
    start_archive_prepare: Callable[[], None]
    start_auto_scan: Callable[[], None]
    show_rtss_notice: Callable[[], None]
    show_startup_warning_popup: Callable[[str, Callable[[], None]], None]


@dataclass(frozen=True)
class StartupPopupEntry:
    popup_id: str
    priority: int
    order: int
    blocking: bool
    show: PopupShowCallback


class StartupFlowController:
    def __init__(
        self,
        *,
        root: Any,
        callbacks: StartupFlowCallbacks,
        is_multi_gpu_blocked: BlockedStateProvider,
        get_startup_warning_text: WarningTextProvider,
        logger=None,
    ) -> None:
        self._root = root
        self._callbacks = callbacks
        self._is_multi_gpu_blocked = is_multi_gpu_blocked
        self._get_startup_warning_text = get_startup_warning_text
        self._logger = logger or logging.getLogger()

        self._post_sheet_startup_done = False
        self._startup_popup_queue: list[StartupPopupEntry] = []
        self._startup_popup_active = False
        self._startup_popup_order = 0

    def mark_post_sheet_startup_done(self) -> None:
        self._post_sheet_startup_done = True

    def enqueue_popup(
        self,
        popup_id: str,
        *,
        priority: int,
        show_callback: PopupShowCallback,
        blocking: bool = False,
    ) -> None:
        self._startup_popup_order += 1
        self._startup_popup_queue.append(
            StartupPopupEntry(
                popup_id=str(popup_id or "unknown"),
                priority=int(priority),
                order=int(self._startup_popup_order),
                blocking=bool(blocking),
                show=show_callback,
            )
        )

    def run_next_popup(self) -> None:
        if self._startup_popup_active:
            return
        if not self._startup_popup_queue:
            return

        self._startup_popup_queue.sort(key=lambda item: (-item.priority, item.order))
        popup_item = self._startup_popup_queue.pop(0)
        if not callable(popup_item.show):
            self._logger.warning("[APP] Startup popup %s has no callable show callback", popup_item.popup_id)
            self._root.after_idle(self.run_next_popup)
            return

        self._startup_popup_active = True
        finished = False

        def _finish_popup() -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._startup_popup_active = False
            self._root.after_idle(self.run_next_popup)

        try:
            if popup_item.blocking:
                popup_item.show()
                _finish_popup()
            else:
                popup_item.show(_finish_popup)
        except Exception:
            self._logger.exception("[APP] Failed to show startup popup: %s", popup_item.popup_id)
            _finish_popup()

    def run_post_sheet_startup(self, ok: bool) -> None:
        if self._post_sheet_startup_done:
            return

        self._post_sheet_startup_done = True
        if self._is_multi_gpu_blocked():
            return
        self._startup_popup_queue.clear()
        self._startup_popup_active = False

        # RTSS popup disabled: settings are now applied automatically after install
        # self.enqueue_popup(
        #     "rtss_notice",
        #     priority=100,
        #     blocking=True,
        #     show_callback=self._callbacks.show_rtss_notice,
        # )

        if not ok:
            self.run_next_popup()
            return

        self._callbacks.start_archive_prepare()
        self._callbacks.start_auto_scan()

        warning_text = str(self._get_startup_warning_text() or "").strip()
        if warning_text:
            self.enqueue_popup(
                "startup_warning",
                priority=80,
                blocking=False,
                show_callback=lambda done_callback, warning=warning_text: self._callbacks.show_startup_warning_popup(
                    warning,
                    done_callback,
                ),
            )

        self.run_next_popup()
