from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CardGridPlacement:
    index: int
    row: int
    column: int


def clamp_grid_columns(requested_cols: int, max_cols: int) -> int:
    return min(max(1, int(requested_cols)), max(1, int(max_cols)))


def get_card_grid_placement(index: int, cols: int) -> CardGridPlacement:
    normalized_cols = max(1, int(cols))
    normalized_index = max(0, int(index))
    return CardGridPlacement(
        index=normalized_index,
        row=normalized_index // normalized_cols,
        column=normalized_index % normalized_cols,
    )


def build_card_grid_placements(total_items: int, cols: int) -> tuple[CardGridPlacement, ...]:
    normalized_total = max(0, int(total_items))
    normalized_cols = max(1, int(cols))
    return tuple(get_card_grid_placement(index, normalized_cols) for index in range(normalized_total))


def compute_visible_game_indices(
    total_items: int,
    cols: int,
    *,
    visible_row_count: int,
    yview_start: float | None = None,
    yview_end: float | None = None,
) -> set[int]:
    normalized_total = max(0, int(total_items))
    if normalized_total == 0:
        return set()

    normalized_cols = max(1, int(cols))
    normalized_visible_rows = max(0, int(visible_row_count))
    total_rows = max(1, math.ceil(normalized_total / normalized_cols))
    start_row = 0
    end_row = min(total_rows - 1, normalized_visible_rows)

    if yview_start is not None and yview_end is not None:
        start_row = max(0, int(float(yview_start) * total_rows))
        end_row = min(total_rows - 1, int(math.ceil(float(yview_end) * total_rows)))

    visible_indices: set[int] = set()
    for row in range(start_row, end_row + 1):
        for column in range(normalized_cols):
            index = (row * normalized_cols) + column
            if index < normalized_total:
                visible_indices.add(index)
    return visible_indices


__all__ = [
    "CardGridPlacement",
    "build_card_grid_placements",
    "clamp_grid_columns",
    "compute_visible_game_indices",
    "get_card_grid_placement",
]
