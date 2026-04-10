from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass
import logging
from tkinter import messagebox
from typing import Any

from installer.games.handlers import get_game_handler
from installer.games.handlers.install_precheck import RESHADE_INSTALL_MODE_DISABLED, SPECIALK_INSTALL_MODE_DISABLED
from installer.install import build_install_context, create_install_workflow_callbacks, run_install_workflow

from .install_entry import InstallEntryDecision, InstallEntryState, validate_install_entry
from .install_selection_controller import InstallSelectionPrecheckOutcome
from .install_state import build_install_entry_state, build_selected_game_snapshot
from .runtime_state import (
    ArchiveRuntimeState,
    CardUiRuntimeState,
    GpuRuntimeState,
    InstallRuntimeState,
    SheetRuntimeState,
)


@dataclass(frozen=True)
class InstallFlowCallbacks:
    get_found_games: Callable[[], Sequence[Mapping[str, Any]]]
    get_lang: Callable[[], str]
    should_apply_fsr4_for_game: Callable[[Mapping[str, Any]], bool]
    update_install_button_state: Callable[[], None]
    install_worker_entry: Callable[..., None]
    finish_install: Callable[[bool, str, Mapping[str, Any] | None], None]
    show_after_install_popup: Callable[[Mapping[str, Any]], None]
    show_info: Callable[[str, str], None]
    show_warning: Callable[[str, str], None]
    show_error: Callable[[str, str], None]


class InstallFlowController:
    def __init__(
        self,
        *,
        app_ref: Any,
        root: Any,
        task_executor: Executor,
        strings: Any,
        archive_state: ArchiveRuntimeState,
        gpu_state: GpuRuntimeState,
        sheet_state: SheetRuntimeState,
        install_state: InstallRuntimeState,
        card_ui_state: CardUiRuntimeState,
        callbacks: InstallFlowCallbacks,
        optipatcher_url: str,
        create_prefixed_logger: Callable[[str], Any],
        logger=None,
    ) -> None:
        self._app_ref = app_ref
        self._root = root
        self._task_executor = task_executor
        self._txt = strings
        self._archive_state = archive_state
        self._gpu_state = gpu_state
        self._sheet_state = sheet_state
        self._install_state = install_state
        self._card_ui_state = card_ui_state
        self._callbacks = callbacks
        self._optipatcher_url = str(optipatcher_url or "")
        self._create_prefixed_logger = create_prefixed_logger
        self._logger = logger or logging.getLogger()

    def run_install_precheck(self, game_data: Mapping[str, Any]) -> InstallSelectionPrecheckOutcome:
        logger = self._create_prefixed_logger(str(game_data.get("game_name", "unknown")).strip() or "unknown")
        handler = get_game_handler(game_data)
        self._install_state.precheck_ual_detected_names = ()
        self._install_state.precheck_reshade_install_mode = RESHADE_INSTALL_MODE_DISABLED
        self._install_state.precheck_reshade_source_dll_name = ""
        self._install_state.precheck_specialk_install_mode = SPECIALK_INSTALL_MODE_DISABLED
        self._install_state.precheck_specialk_source_dll_name = ""
        try:
            logger.info("Running install precheck with handler: %s", getattr(handler, "handler_key", "default"))
            precheck = handler.run_install_precheck(game_data, self._is_korean(), logger)
            notice_message = handler.format_precheck_notice(precheck, self._is_korean())
            conflict_findings = tuple(getattr(precheck, "conflict_findings", ()) or ())
            if not conflict_findings:
                logger.info("[MOD] No MOD detected")

            for finding in conflict_findings:
                mod_name = {
                    "reshade": "ReShade",
                    "special_k": "Special K",
                    "ultimate_asi_loader": "Ultimate ASI Loader",
                    "renodx": "RenoDX",
                }.get(str(getattr(finding, "kind", "") or "").strip().lower(), "MOD")
                evidence = ", ".join(str(item).strip() for item in tuple(getattr(finding, "evidence", ()) or ()) if str(item).strip())
                if evidence:
                    logger.info("[MOD] %s (%s) detected", mod_name, evidence)
                else:
                    logger.info("[MOD] %s detected", mod_name)
            self._install_state.precheck_reshade_install_mode = str(
                getattr(precheck, "reshade_install_mode", RESHADE_INSTALL_MODE_DISABLED) or RESHADE_INSTALL_MODE_DISABLED
            )
            self._install_state.precheck_reshade_source_dll_name = str(
                getattr(precheck, "reshade_source_dll_name", "") or ""
            )
            self._install_state.precheck_specialk_install_mode = str(
                getattr(precheck, "specialk_install_mode", SPECIALK_INSTALL_MODE_DISABLED)
                or SPECIALK_INSTALL_MODE_DISABLED
            )
            self._install_state.precheck_specialk_source_dll_name = str(
                getattr(precheck, "specialk_source_dll_name", "") or ""
            )
            if precheck.ok:
                resolved_dll_name = str(precheck.resolved_dll_name or "")
                logger.info("Install precheck resolved DLL name: %s", resolved_dll_name)

                ual_detected_names: tuple[str, ...] = ()
                for finding in conflict_findings:
                    if str(getattr(finding, "kind", "") or "").strip().lower() == "ultimate_asi_loader":
                        ual_detected_names = tuple(
                            str(item).strip()
                            for item in (getattr(finding, "evidence", ()) or ())
                            if str(item).strip()
                        )
                        break
                self._install_state.precheck_ual_detected_names = ual_detected_names
                if ual_detected_names:
                    logger.info("[UAL] Detected UAL DLL(s): %s", ", ".join(ual_detected_names))
                if self._install_state.precheck_reshade_install_mode != RESHADE_INSTALL_MODE_DISABLED:
                    logger.info(
                        "[ReShade] Install mode: %s (%s)",
                        self._install_state.precheck_reshade_install_mode,
                        self._install_state.precheck_reshade_source_dll_name or "ReShade64.dll",
                    )
                if self._install_state.precheck_specialk_install_mode != SPECIALK_INSTALL_MODE_DISABLED:
                    logger.info(
                        "[SpecialK] Install mode: %s (%s)",
                        self._install_state.precheck_specialk_install_mode,
                        self._install_state.precheck_specialk_source_dll_name or "SpecialK64.dll",
                    )

                return InstallSelectionPrecheckOutcome(
                    ok=True,
                    resolved_dll_name=resolved_dll_name,
                    mod_notice_message=notice_message,
                )
            formatted_error = handler.format_precheck_error(precheck, self._is_korean())
            popup_message = handler.get_precheck_popup_message(precheck, self._is_korean())
            logger.warning("Install precheck failed: %s", precheck.raw_error_message)
            return InstallSelectionPrecheckOutcome(
                ok=False,
                error=formatted_error,
                popup_message=popup_message,
                mod_notice_message=notice_message,
            )
        except Exception as exc:
            logger.exception("Install precheck failed unexpectedly: %s", exc)
            return InstallSelectionPrecheckOutcome(
                ok=False,
                error=str(exc),
            )

    def build_install_entry_state(self) -> InstallEntryState:
        selection = build_selected_game_snapshot(
            self._callbacks.get_found_games(),
            self._card_ui_state.selected_game_index,
            self._callbacks.get_lang(),
        )
        archive = self._archive_state
        predownload_in_progress = bool(
            archive.optipatcher_downloading
            or archive.specialk_downloading
            or archive.ual_downloading
            or archive.unreal5_downloading
        )
        return build_install_entry_state(
            selection=selection,
            multi_gpu_blocked=bool(self._gpu_state.multi_gpu_blocked),
            install_in_progress=bool(self._install_state.in_progress),
            optiscaler_archive_downloading=bool(archive.optiscaler_downloading),
            install_precheck_running=bool(self._install_state.precheck_running),
            install_precheck_ok=bool(self._install_state.precheck_ok),
            install_precheck_error=str(self._install_state.precheck_error or ""),
            install_precheck_dll_name=str(self._install_state.precheck_dll_name or ""),
            optiscaler_archive_ready=bool(archive.optiscaler_ready),
            opti_source_archive=str(archive.opti_source_archive or ""),
            optiscaler_archive_error=str(archive.optiscaler_error or ""),
            fsr4_archive_downloading=bool(archive.fsr4_downloading),
            fsr4_archive_ready=bool(archive.fsr4_ready),
            fsr4_source_archive=str(archive.fsr4_source_archive or ""),
            fsr4_archive_error=str(archive.fsr4_error or ""),
            game_popup_confirmed=bool(self._install_state.popup_confirmed),
            predownload_in_progress=predownload_in_progress,
            ual_cached_archive=str(archive.ual_source_archive or "") if archive.ual_ready else "",
            optipatcher_cached_archive=str(archive.optipatcher_source_archive or "") if archive.optipatcher_ready else "",
            specialk_cached_archive=str(archive.specialk_source_archive or "") if archive.specialk_ready else "",
            unreal5_cached_archive=str(archive.unreal5_source_archive or "") if archive.unreal5_ready else "",
        )

    def show_install_entry_rejection(self, decision: InstallEntryDecision) -> None:
        if decision.code in {"multi_gpu_blocked", "install_precheck_running"}:
            return

        if decision.code == "install_in_progress":
            self._callbacks.show_info(self._txt.dialogs.installing_title, self._txt.dialogs.installing_body)
            return

        if decision.code == "predownload_in_progress":
            self._callbacks.show_info(self._txt.dialogs.preparing_download_title, self._txt.dialogs.preparing_download_body)
            return

        if decision.code == "no_game_selected":
            self._callbacks.show_warning(self._txt.common.warning, self._txt.dialogs.select_game_card_body)
            return

        if decision.code == "optiscaler_archive_downloading":
            self._callbacks.show_info(self._txt.dialogs.preparing_archive_title, self._txt.dialogs.preparing_archive_body)
            return

        if decision.code == "precheck_incomplete":
            detail = decision.detail or self._txt.dialogs.precheck_incomplete_body
            detail = f"{detail}\n\n{self._txt.dialogs.precheck_retry_mods_body}"
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == "optiscaler_archive_not_ready":
            detail = decision.detail or self._txt.dialogs.optiscaler_archive_not_ready
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == "invalid_game_selection":
            self._callbacks.show_warning(self._txt.common.warning, self._txt.dialogs.invalid_game_body)
            return

        if decision.code == "fsr4_archive_downloading":
            self._callbacks.show_info(self._txt.dialogs.preparing_download_title, self._txt.dialogs.preparing_download_body)
            return

        if decision.code == "fsr4_not_ready":
            detail = decision.detail or self._txt.dialogs.fsr4_not_ready
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == "confirm_popup_required":
            self._callbacks.show_warning(self._txt.common.notice, self._txt.dialogs.confirm_popup_body)

    def apply_selected_install(self) -> None:
        decision = validate_install_entry(
            self.build_install_entry_state(),
            self._callbacks.should_apply_fsr4_for_game,
        )
        if not decision.ok:
            self.show_install_entry_rejection(decision)
            return

        game_data = dict(decision.selected_game or {})
        source_archive = decision.source_archive
        resolved_dll_name = decision.resolved_dll_name
        fsr4_source_archive = decision.fsr4_source_archive
        ual_cached_archive = decision.ual_cached_archive
        optipatcher_cached_archive = decision.optipatcher_cached_archive
        specialk_cached_archive = decision.specialk_cached_archive
        unreal5_cached_archive = decision.unreal5_cached_archive

        self._install_state.in_progress = True
        self._callbacks.update_install_button_state()
        self._task_executor.submit(
            self._callbacks.install_worker_entry,
            game_data,
            source_archive,
            resolved_dll_name,
            fsr4_source_archive,
            decision.fsr4_required,
            ual_cached_archive,
            optipatcher_cached_archive,
            specialk_cached_archive,
            unreal5_cached_archive,
        )

    def run_install_worker(
        self,
        game_data: Mapping[str, Any],
        source_archive: str,
        resolved_dll_name: str,
        fsr4_source_archive: str,
        fsr4_required: bool,
        ual_cached_archive: str = "",
        optipatcher_cached_archive: str = "",
        specialk_cached_archive: str = "",
        unreal5_cached_archive: str = "",
    ) -> None:
        game_name = str(game_data.get("game_name", "unknown")).strip() or "unknown"
        logger = self._create_prefixed_logger(game_name)
        try:
            ual_detected_names = tuple(self._install_state.precheck_ual_detected_names or ())
            reshade_install_mode = str(
                self._install_state.precheck_reshade_install_mode or RESHADE_INSTALL_MODE_DISABLED
            )
            reshade_source_dll_name = str(self._install_state.precheck_reshade_source_dll_name or "")
            specialk_install_mode = str(
                self._install_state.precheck_specialk_install_mode or SPECIALK_INSTALL_MODE_DISABLED
            )
            specialk_source_dll_name = str(self._install_state.precheck_specialk_source_dll_name or "")
            install_ctx = build_install_context(
                self._app_ref,
                game_data,
                source_archive,
                resolved_dll_name,
                fsr4_source_archive,
                fsr4_required,
                ual_detected_names,
                logger,
                reshade_install_mode=reshade_install_mode,
                reshade_source_dll_name=reshade_source_dll_name,
                specialk_install_mode=specialk_install_mode,
                specialk_source_dll_name=specialk_source_dll_name,
            )
            installed_game = run_install_workflow(
                self._app_ref,
                install_ctx,
                self._sheet_state.module_download_links,
                self._optipatcher_url,
                self._gpu_state.gpu_info,
                create_install_workflow_callbacks(),
                logger,
                ual_cached_archive=ual_cached_archive,
                optipatcher_cached_archive=optipatcher_cached_archive,
                specialk_cached_archive=specialk_cached_archive,
                unreal5_cached_archive=unreal5_cached_archive,
            )
            self._root.after(
                0,
                lambda game=dict(installed_game): self._callbacks.finish_install(True, "Install Completed", game),
            )
        except RuntimeError as exc:
            self._root.after(
                0,
                lambda err=exc, game=dict(game_data): self._callbacks.finish_install(False, str(err), game),
            )
        except Exception as exc:
            logger.exception("Install failed: %s", exc)
            self._root.after(
                0,
                lambda err=exc, game=dict(game_data): self._callbacks.finish_install(False, str(err), game),
            )

    def on_install_finished(self, success: bool, message: str, installed_game=None) -> None:
        self._install_state.in_progress = False
        self._callbacks.update_install_button_state()

        if success:
            game = installed_game if isinstance(installed_game, dict) else {}
            self._root.after_idle(lambda g=dict(game): self._callbacks.show_after_install_popup(g))
            return

        self._callbacks.show_error(
            self._txt.common.error,
            self._txt.dialogs.install_failed_body_template.format(message=message),
        )

    def _is_korean(self) -> bool:
        return str(self._callbacks.get_lang() or "").lower() == "ko"


def create_install_flow_controller(
    app: Any,
    *,
    optipatcher_url: str,
    create_prefixed_logger: Callable[[str], Any],
) -> InstallFlowController:
    return InstallFlowController(
        app_ref=app,
        root=app.root,
        task_executor=app._task_executor,
        strings=app.txt,
        archive_state=app.archive_state,
        gpu_state=app.gpu_state,
        sheet_state=app.sheet_state,
        install_state=app.install_state,
        card_ui_state=app.card_ui_state,
        callbacks=InstallFlowCallbacks(
            get_found_games=lambda: tuple(app.found_exe_list),
            get_lang=lambda: app.lang,
            should_apply_fsr4_for_game=app._should_apply_fsr4_for_game,
            update_install_button_state=app._update_install_button_state,
            install_worker_entry=app._apply_optiscaler_worker,
            finish_install=app._on_install_finished,
            show_after_install_popup=app._show_after_install_popup,
            show_info=messagebox.showinfo,
            show_warning=messagebox.showwarning,
            show_error=messagebox.showerror,
        ),
        optipatcher_url=optipatcher_url,
        create_prefixed_logger=create_prefixed_logger,
        logger=logging.getLogger(),
    )


__all__ = [
    "InstallFlowCallbacks",
    "InstallFlowController",
    "create_install_flow_controller",
]
