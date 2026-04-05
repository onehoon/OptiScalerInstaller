from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
import tkinter as tk
from typing import Any

from .card_grid import build_card_grid_placements, clamp_grid_columns
from .card_layout import compute_card_overflow_fit_decision, compute_card_resize_reflow_decision


@dataclass
class CardViewportRuntime:
    grid_cols_current: int
    resize_after_id: str | None = None
    resize_visual_after_id: str | None = None
    resize_in_progress: bool = False
    last_reflow_width: int = 0
    base_root_width: int | None = None
    games_scrollregion_after_id: str | None = None
    games_viewport_after_id: str | None = None
    overflow_fit_after_id: str | None = None


@dataclass(frozen=True)
class CardViewportCallbacks:
    get_card_frames: Callable[[], Sequence[Any]]
    has_found_games: Callable[[], bool]
    render_cards: Callable[[bool], None]
    get_effective_widget_scale: Callable[[], float]


class CardViewportController:
    def __init__(
        self,
        *,
        root: Any,
        games_scroll: Any,
        poster_queue: Any,
        runtime: CardViewportRuntime,
        callbacks: CardViewportCallbacks,
        card_width: int,
        card_h_spacing: int,
        card_v_spacing: int,
        startup_safe_margin_px: int = 240,
        logger=None,
    ) -> None:
        self._root = root
        self._games_scroll = games_scroll
        self._poster_queue = poster_queue
        self._runtime = runtime
        self._callbacks = callbacks
        self._card_width = int(card_width)
        self._card_h_spacing = int(card_h_spacing)
        self._card_v_spacing = int(card_v_spacing)
        self._startup_safe_margin_px = int(startup_safe_margin_px)
        self._logger = logger or logging.getLogger()

    def capture_startup_width(self) -> None:
        self._runtime.base_root_width = max(1, int(self._root.winfo_width() or 0))
        self._runtime.last_reflow_width = max(1, int(self._root.winfo_width() or 0))
        self._schedule_overflow_fit_check()
        if self._callbacks.has_found_games():
            self._callbacks.render_cards(True)

    def on_root_resize(self, _event=None) -> None:
        self._schedule_reflow_for_resize()

    def on_games_area_resize(self, _event=None) -> None:
        self._schedule_reflow_for_resize()
        self._schedule_overflow_fit_check()
        if not self._runtime.resize_in_progress:
            self._poster_queue.pump()

    def on_games_scrollbar_command(self, *args) -> None:
        canvas = getattr(self._games_scroll, "_parent_canvas", None)
        if canvas is None or not args:
            return

        try:
            canvas.yview(*args)
        except Exception:
            return

        self._schedule_games_viewport_update()

    def on_games_scroll(self, _event=None) -> None:
        event = _event
        canvas = getattr(self._games_scroll, "_parent_canvas", None)
        if canvas is not None and event is not None:
            step = 0
            if hasattr(event, "delta") and event.delta:
                step = -1 if event.delta > 0 else 1
            elif getattr(event, "num", None) == 4:
                step = -1
            elif getattr(event, "num", None) == 5:
                step = 1

            if step != 0:
                canvas.yview_scroll(step, "units")

        self._schedule_games_viewport_update()

    def fit_cards_to_visible_width(self, preferred_cols: int | None = None) -> None:
        card_frames = tuple(self._callbacks.get_card_frames() or ())
        if not card_frames:
            if preferred_cols is not None:
                self.configure_card_columns(max(1, int(preferred_cols)))
            return

        requested_cols = max(
            1,
            int(preferred_cols if preferred_cols is not None else self._runtime.grid_cols_current),
        )
        max_cols = self._max_safe_columns_for_width(self._get_forced_card_area_width())
        cols = clamp_grid_columns(requested_cols, max_cols)
        self._layout_existing_cards(cols)
        self.schedule_games_scrollregion_refresh()
        self._schedule_overflow_fit_check()

    def _get_forced_card_area_width(self) -> int:
        canvas = getattr(self._games_scroll, "_parent_canvas", None)
        if canvas is not None:
            width = int(canvas.winfo_width() or 0)
            if width > 1:
                return width

        try:
            scroll_w = int(self._games_scroll.winfo_width() or 0)
            if scroll_w > 1:
                return scroll_w
        except Exception:
            pass

        window_w = max(1, int(self._root.winfo_width() or 0))
        scale = float(self._callbacks.get_effective_widget_scale())
        safe_margin = int(round(self._startup_safe_margin_px * scale))
        return max(1, window_w - safe_margin)

    def _get_dynamic_column_count(self) -> int:
        usable_w = self._get_forced_card_area_width()
        if usable_w <= 1:
            return 1
        return self._max_safe_columns_for_width(usable_w)

    def _max_safe_columns_for_width(self, usable_w: int) -> int:
        card_unit_w = max(1, self._get_card_slot_width())
        safe_w = max(1, int(usable_w) - 6)
        cols = max(1, safe_w // card_unit_w)
        return cols

    def _get_card_slot_width(self) -> int:
        fallback = max(1, self._card_width + self._card_h_spacing)
        card_frames = tuple(self._callbacks.get_card_frames() or ())
        if not card_frames:
            return fallback

        card = card_frames[0]
        try:
            card_w = max(
                int(card.winfo_width() or 0),
                int(card.winfo_reqwidth() or 0),
                self._card_width,
            )
            grid_info = card.grid_info()
            padx = grid_info.get("padx", (self._card_h_spacing // 2, self._card_h_spacing // 2))

            left = 0
            right = 0
            if isinstance(padx, (tuple, list)):
                if len(padx) >= 2:
                    left = int(padx[0])
                    right = int(padx[1])
                elif len(padx) == 1:
                    left = right = int(padx[0])
            elif isinstance(padx, str):
                parts = [p for p in padx.replace("{", " ").replace("}", " ").split() if p]
                if len(parts) >= 2:
                    left = int(float(parts[0]))
                    right = int(float(parts[1]))
                elif len(parts) == 1:
                    left = right = int(float(parts[0]))
            else:
                left = right = int(padx)

            return max(1, card_w + left + right)
        except Exception:
            return fallback

    def configure_card_columns(self, cols: int) -> None:
        normalized_cols = max(1, int(cols))
        max_cols = max(int(self._runtime.grid_cols_current), normalized_cols)
        for col in range(max_cols):
            self._games_scroll.grid_columnconfigure(col, weight=0, minsize=0)

        for col in range(normalized_cols):
            self._games_scroll.grid_columnconfigure(col, weight=0, minsize=self._card_width)
        self._runtime.grid_cols_current = normalized_cols

    def _layout_existing_cards(self, cols: int) -> None:
        normalized_cols = max(1, int(cols))
        card_frames = tuple(self._callbacks.get_card_frames() or ())
        self.configure_card_columns(normalized_cols)
        for placement, card in zip(build_card_grid_placements(len(card_frames), normalized_cols), card_frames):
            card.grid(
                row=placement.row,
                column=placement.column,
                padx=(self._card_h_spacing // 2, self._card_h_spacing // 2),
                pady=(self._card_v_spacing // 2, self._card_v_spacing // 2),
                sticky="n",
            )

    def _cards_overflow_visible_width(self) -> bool:
        card_frames = tuple(self._callbacks.get_card_frames() or ())
        if not card_frames:
            return False

        canvas = getattr(self._games_scroll, "_parent_canvas", None)
        if canvas is None:
            return False

        viewport_w = max(1, int(canvas.winfo_width() or 0))
        max_right = 0
        for card in card_frames:
            try:
                right = int(card.winfo_x() + card.winfo_width())
                if right > max_right:
                    max_right = right
            except Exception:
                continue
        return max_right > viewport_w

    def schedule_games_scrollregion_refresh(self) -> None:
        if self._runtime.games_scrollregion_after_id is not None:
            return
        self._runtime.games_scrollregion_after_id = self._root.after_idle(self._refresh_games_scrollregion)

    def _refresh_games_scrollregion(self) -> None:
        self._runtime.games_scrollregion_after_id = None
        try:
            canvas = getattr(self._games_scroll, "_parent_canvas", None)
            if canvas is not None:
                bbox = canvas.bbox("all")
                if bbox:
                    canvas.configure(scrollregion=bbox)
        except Exception:
            pass

    def _schedule_overflow_fit_check(self) -> None:
        if self._runtime.overflow_fit_after_id is not None:
            return
        try:
            if not self._root.winfo_exists() or not self._games_scroll.winfo_exists():
                return
        except tk.TclError:
            return
        self._runtime.overflow_fit_after_id = self._root.after_idle(self._run_overflow_fit_check)

    def _run_overflow_fit_check(self) -> None:
        self._runtime.overflow_fit_after_id = None
        try:
            card_frames = tuple(self._callbacks.get_card_frames() or ())
            if not self._root.winfo_exists() or not self._games_scroll.winfo_exists() or not card_frames:
                return

            canvas = getattr(self._games_scroll, "_parent_canvas", None)
            viewport_w = int(canvas.winfo_width() or 0) if canvas is not None else 0
            cols = max(1, int(self._runtime.grid_cols_current))
            decision = compute_card_overflow_fit_decision(
                viewport_width=viewport_w,
                current_cols=cols,
                max_cols=self._max_safe_columns_for_width(self._get_forced_card_area_width()),
                overflow_detected=self._cards_overflow_visible_width(),
            )
            if decision.retry_delay_ms is not None:
                self._runtime.overflow_fit_after_id = self._root.after(
                    decision.retry_delay_ms,
                    self._run_overflow_fit_check,
                )
                return

            if decision.relayout_cols is not None:
                self._layout_existing_cards(decision.relayout_cols)
                self.schedule_games_scrollregion_refresh()
                if decision.should_reschedule_check:
                    self._schedule_overflow_fit_check()
        except tk.TclError:
            self._logger.debug("Skipped overflow fit check because widgets are no longer available")

    def _schedule_reflow_for_resize(self) -> None:
        current_w = max(1, int(self._root.winfo_width() or 0))
        self._runtime.resize_in_progress = True

        decision = compute_card_resize_reflow_decision(
            current_width=current_w,
            last_reflow_width=self._runtime.last_reflow_width,
            next_cols=self._get_dynamic_column_count(),
            current_cols=self._runtime.grid_cols_current,
        )
        if decision.next_last_reflow_width is not None:
            self._runtime.last_reflow_width = int(decision.next_last_reflow_width)
        if not decision.should_schedule_reflow:
            if decision.clear_resize_in_progress:
                self._runtime.resize_in_progress = False
            if decision.should_schedule_overflow_check:
                self._schedule_overflow_fit_check()
            return

        if self._runtime.resize_after_id is not None:
            self._root.after_cancel(self._runtime.resize_after_id)
        self._runtime.resize_after_id = self._root.after(decision.delay_ms, self._finish_resize_reflow)

        if self._runtime.resize_visual_after_id is not None:
            self._root.after_cancel(self._runtime.resize_visual_after_id)
        self._runtime.resize_visual_after_id = self._root.after(
            decision.visual_delay_ms,
            self._end_resize_visual_suppression,
        )

    def _finish_resize_reflow(self) -> None:
        self._runtime.resize_after_id = None
        self._rerender_cards_for_resize()

    def _end_resize_visual_suppression(self) -> None:
        self._runtime.resize_visual_after_id = None
        self._runtime.resize_in_progress = False
        self._poster_queue.pump()

    def _schedule_games_viewport_update(self, delay_ms: int = 30) -> None:
        try:
            if self._runtime.games_viewport_after_id is not None:
                self._root.after_cancel(self._runtime.games_viewport_after_id)
            self._runtime.games_viewport_after_id = self._root.after(
                max(0, int(delay_ms)),
                self._run_games_viewport_update,
            )
        except Exception:
            self._runtime.games_viewport_after_id = None

    def _run_games_viewport_update(self) -> None:
        self._runtime.games_viewport_after_id = None
        self._poster_queue.pump()

    def _rerender_cards_for_resize(self) -> None:
        self._runtime.resize_after_id = None
        cols = self._get_dynamic_column_count()
        self.fit_cards_to_visible_width(cols)


__all__ = [
    "CardViewportCallbacks",
    "CardViewportController",
    "CardViewportRuntime",
]
