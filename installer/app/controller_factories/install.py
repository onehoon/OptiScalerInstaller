from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Any

from installer.i18n import build_install_information_text, build_install_selection_popup_text

from ..app_runtime_actions import apply_install_selection_state
from ..card_runtime_actions import apply_selected_game_index, run_install_precheck
from ..install_flow import InstallFlowController, create_install_flow_controller
from ..install_runtime_actions import update_install_button_state
from ..install_selection_controller import InstallSelectionCallbacks, InstallSelectionController
from ..notice_controller import AppNoticeController
from ..ui_shell_actions import set_information_text


def build_install_controllers(
    app: Any,
    config: Any,
    *,
    app_notice: AppNoticeController,
) -> tuple[InstallFlowController, InstallSelectionController]:
    install_flow = _build_install_flow_controller(app, config)
    install_selection = _build_install_selection_controller(
        schedule=app.root.after_idle,
        callbacks=InstallSelectionCallbacks(
            apply_selected_index=lambda index: apply_selected_game_index(app, index),
            set_information_text=lambda text="": set_information_text(app, text),
            build_information_text=lambda game: build_install_information_text(
                game,
                lang=app.lang,
                rtss_game_overlay_notice=_resolve_rtss_game_overlay_notice(
                    game=game,
                    install_state=app.install_state,
                    dialogs_strings=app.txt.dialogs,
                ),
            ),
            apply_ui_state=lambda state: apply_install_selection_state(app, state),
            update_install_button_state=lambda: update_install_button_state(app),
            run_precheck=lambda game: run_install_precheck(app, game),
            get_selection_popup_message=lambda game: _build_selection_popup_message(
                game=game,
                lang=app.lang,
                install_state=app.install_state,
                dialogs_strings=app.txt.dialogs,
            ),
            show_selection_popup=app_notice.show_selection_popup,
            show_precheck_popup=app_notice.show_precheck_popup,
        ),
    )
    return install_flow, install_selection


def _build_install_flow_controller(app: Any, config: Any) -> InstallFlowController:
    return create_install_flow_controller(
        app,
        create_prefixed_logger=config.create_prefixed_logger,
    )


def _build_selection_popup_message(
    *,
    game: Mapping[str, Any],
    lang: str,
    install_state: Any,
    dialogs_strings: Any,
) -> str:
    rtss_notice = _resolve_rtss_game_overlay_notice(
        game=game,
        install_state=install_state,
        dialogs_strings=dialogs_strings,
    )

    return build_install_selection_popup_text(
        game,
        lang=lang,
        rtss_game_overlay_notice=rtss_notice,
    )


def _resolve_rtss_game_overlay_notice(
    *,
    game: Mapping[str, Any],
    install_state: Any,
    dialogs_strings: Any,
) -> str:
    if bool((game or {}).get("rtss_overlay")) and bool(getattr(install_state, "rtss_installed", False)) and bool(
        getattr(install_state, "rtss_profiles_global_exists", False)
    ):
        return str(getattr(dialogs_strings, "rtss_game_overlay_notice", "") or "")
    return ""


def _build_install_selection_controller(
    *,
    schedule: Callable[[Callable[[], None]], Any],
    callbacks: InstallSelectionCallbacks,
) -> InstallSelectionController:
    return InstallSelectionController(
        schedule=schedule,
        callbacks=callbacks,
        logger=logging.getLogger(),
    )


__all__ = [
    "build_install_controllers",
]
