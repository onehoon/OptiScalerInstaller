from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from installer.i18n import build_install_information_text

from .app_runtime_actions import apply_install_selection_state
from .install_selection_controller import InstallSelectionPrecheckOutcome, InstallSelectionUiState
from .install_runtime_actions import update_install_button_state
from .startup_window import get_ctk_scale
from .ui_shell_actions import set_information_text, update_selected_game_header


def reset_selected_game_state(app: Any) -> None:
    app.card_ui_state.selected_game_index = None
    apply_install_selection_state(
        app,
        InstallSelectionUiState(
            popup_confirmed=False,
            precheck_running=False,
            precheck_ok=False,
        )
    )
    set_information_text(app, "")


def apply_selected_game_index(app: Any, index: int) -> None:
    app.card_ui_state.selected_game_index = int(index)
    update_selected_game_header(app)
    controller = getattr(app, "_card_ui_controller", None)
    if controller is not None:
        controller.refresh_all_card_visuals()


def clear_rendered_cards(app: Any) -> None:
    app._poster_queue.begin_new_render()
    for frame in app.card_frames:
        frame.destroy()
    app.card_frames.clear()
    app.card_items.clear()
    app._ctk_images.clear()
    app.card_ui_state.hovered_card_index = None


def clear_cards(app: Any, keep_selection: bool = False) -> None:
    clear_rendered_cards(app)
    if not keep_selection:
        reset_selected_game_state(app)
    update_selected_game_header(app)
    update_install_button_state(app)


def reset_scan_results_for_new_scan(app: Any) -> None:
    app.found_exe_list = []
    clear_cards(app)
    configure_card_columns(app, get_dynamic_column_count(app))


def hide_empty_label(app: Any) -> None:
    if hasattr(app, "empty_label") and app.empty_label.winfo_exists():
        app.empty_label.grid_remove()


def append_found_game(app: Any, game: Mapping[str, Any]) -> int:
    index = len(app.found_exe_list)
    app.found_exe_list.append(game)
    return index


def _get_card_spacing(app: Any) -> tuple[int, int]:
    viewport = getattr(app, "_card_viewport_controller", None)
    if viewport is None:
        return 0, 0
    h_spacing = max(0, int(getattr(viewport, "_card_h_spacing", 0) or 0))
    v_spacing = max(0, int(getattr(viewport, "_card_v_spacing", 0) or 0))
    return h_spacing, v_spacing


def create_and_place_card(app: Any, index: int, game: Mapping[str, Any], placement: Any) -> None:
    card = make_card(app, index, game)
    card_h_spacing, card_v_spacing = _get_card_spacing(app)
    card.grid(
        row=placement.row,
        column=placement.column,
        padx=(card_h_spacing // 2, card_h_spacing // 2),
        pady=(card_v_spacing // 2, card_v_spacing // 2),
        sticky="nsew",
    )
    app.card_frames.append(card)


def restore_rendered_selection(app: Any, index: int, game: Mapping[str, Any]) -> None:
    app.card_ui_state.selected_game_index = int(index)
    controller = getattr(app, "_card_ui_controller", None)
    if controller is not None:
        controller.refresh_all_card_visuals()
    set_information_text(
        app,
        build_install_information_text(
            game,
            lang=app.lang,
            stage="install_pre",
        ),
    )


def get_effective_widget_scale(app: Any) -> float:
    return get_ctk_scale(app.root, 1.0)


def get_forced_card_area_width(app: Any) -> int:
    return app._card_viewport_controller._get_forced_card_area_width()


def get_dynamic_column_count(app: Any) -> int:
    return app._card_viewport_controller._get_dynamic_column_count()


def max_safe_columns_for_width(app: Any, usable_w: int) -> int:
    return app._card_viewport_controller._max_safe_columns_for_width(usable_w)


def configure_card_columns(app: Any, cols: int):
    return app._card_viewport_controller.configure_card_columns(cols)


def refresh_games_scrollregion(app: Any):
    return app._card_viewport_controller._refresh_games_scrollregion()


def render_cards(app: Any, keep_selection: bool = False):
    controller = getattr(app, "_card_ui_controller", None)
    if controller is None:
        return
    return controller.render_cards(keep_selection=bool(keep_selection))


def make_card(app: Any, index: int, game: Mapping[str, Any]):
    controller = getattr(app, "_card_ui_controller", None)
    if controller is None:
        raise RuntimeError("Game card UI controller is not available")
    return controller.make_card(index, game)


def visible_game_indices(app: Any) -> set[int]:
    controller = getattr(app, "_card_ui_controller", None)
    if controller is None:
        return set()
    return controller.visible_game_indices()


def apply_loaded_poster(app: Any, index: int, label: Any, pil_img: Any):
    controller = getattr(app, "_card_ui_controller", None)
    if controller is None:
        return
    return controller.set_card_base_image(index, label, pil_img)


def set_card_image_updates_suspended(app: Any, suspended: bool) -> None:
    controller = getattr(app, "_card_ui_controller", None)
    if controller is None:
        return
    controller.set_card_image_updates_suspended(bool(suspended))


def select_game(app: Any, index: int):
    controller = getattr(app, "_install_selection_controller", None)
    if controller is None:
        return
    if app.install_state.in_progress:
        return
    controller.select_game(index, tuple(app.found_exe_list))


def run_install_precheck(app: Any, game_data: Mapping[str, Any]) -> InstallSelectionPrecheckOutcome:
    controller = getattr(app, "_install_flow_controller", None)
    if controller is None:
        return InstallSelectionPrecheckOutcome(
            ok=False,
            error="Install flow controller is not available",
        )
    return controller.run_install_precheck(game_data)


def add_game_card_incremental(app: Any, game: Mapping[str, Any]) -> None:
    controller = getattr(app, "_card_render_controller", None)
    if controller is None:
        return
    cols = max(1, get_dynamic_column_count(app))
    controller.add_game_card(
        game,
        cols=cols,
        target_cols=max_safe_columns_for_width(app, get_forced_card_area_width(app)),
    )


__all__ = [
    "add_game_card_incremental",
    "append_found_game",
    "apply_loaded_poster",
    "apply_selected_game_index",
    "clear_cards",
    "clear_rendered_cards",
    "configure_card_columns",
    "create_and_place_card",
    "get_dynamic_column_count",
    "get_effective_widget_scale",
    "get_forced_card_area_width",
    "hide_empty_label",
    "make_card",
    "max_safe_columns_for_width",
    "refresh_games_scrollregion",
    "render_cards",
    "reset_scan_results_for_new_scan",
    "reset_selected_game_state",
    "restore_rendered_selection",
    "run_install_precheck",
    "select_game",
    "set_card_image_updates_suspended",
    "visible_game_indices",
]
