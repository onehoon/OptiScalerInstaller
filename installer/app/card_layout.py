from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardResizeReflowDecision:
    should_schedule_reflow: bool
    delay_ms: int = 0
    visual_delay_ms: int = 0
    should_schedule_overflow_check: bool = False
    clear_resize_in_progress: bool = False
    next_last_reflow_width: int | None = None


@dataclass(frozen=True)
class CardOverflowFitDecision:
    relayout_cols: int | None = None
    retry_delay_ms: int | None = None
    should_reschedule_check: bool = False


def compute_card_resize_reflow_decision(
    *,
    current_width: int,
    last_reflow_width: int,
    next_cols: int,
    current_cols: int,
    width_delta_threshold: int = 20,
    column_change_delay_ms: int = 120,
    same_columns_delay_ms: int = 160,
    visual_delay_offset_ms: int = 80,
) -> CardResizeReflowDecision:
    normalized_current_width = max(1, int(current_width))
    normalized_last_width = max(0, int(last_reflow_width))
    normalized_next_cols = max(1, int(next_cols))
    normalized_current_cols = max(1, int(current_cols))

    if normalized_next_cols != normalized_current_cols:
        return CardResizeReflowDecision(
            should_schedule_reflow=True,
            delay_ms=int(column_change_delay_ms),
            visual_delay_ms=int(column_change_delay_ms + visual_delay_offset_ms),
        )

    if abs(normalized_current_width - normalized_last_width) < int(width_delta_threshold):
        return CardResizeReflowDecision(
            should_schedule_reflow=False,
            should_schedule_overflow_check=True,
            clear_resize_in_progress=True,
        )

    return CardResizeReflowDecision(
        should_schedule_reflow=True,
        delay_ms=int(same_columns_delay_ms),
        visual_delay_ms=int(same_columns_delay_ms + visual_delay_offset_ms),
        next_last_reflow_width=normalized_current_width,
    )


def compute_card_overflow_fit_decision(
    *,
    viewport_width: int,
    current_cols: int,
    max_cols: int,
    overflow_detected: bool,
    retry_delay_ms: int = 30,
) -> CardOverflowFitDecision:
    normalized_viewport_width = int(viewport_width)
    normalized_current_cols = max(1, int(current_cols))
    normalized_max_cols = max(1, int(max_cols))
    overflow = bool(overflow_detected)

    if normalized_viewport_width <= 1:
        return CardOverflowFitDecision(retry_delay_ms=int(retry_delay_ms))

    target_cols = min(normalized_current_cols, normalized_max_cols)
    if normalized_current_cols < normalized_max_cols and not overflow:
        target_cols = normalized_max_cols

    if target_cols != normalized_current_cols:
        return CardOverflowFitDecision(
            relayout_cols=target_cols,
            should_reschedule_check=target_cols < normalized_current_cols,
        )

    if normalized_current_cols > 1 and overflow:
        return CardOverflowFitDecision(
            relayout_cols=normalized_current_cols - 1,
            should_reschedule_check=True,
        )

    return CardOverflowFitDecision()


__all__ = [
    "CardOverflowFitDecision",
    "CardResizeReflowDecision",
    "compute_card_overflow_fit_decision",
    "compute_card_resize_reflow_decision",
]
