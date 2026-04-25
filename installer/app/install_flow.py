from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any

from installer.common.log_sanitizer import redact_text
from .ui_shell_actions import set_information_text

from installer.games.handlers import get_game_handler
from installer.i18n import build_install_information_text
from installer.install import build_install_context, create_install_workflow_callbacks, run_install_workflow

from .install_entry import InstallEntryDecision, InstallEntryState, validate_install_entry
from .install_rejection_codes import (
    INSTALL_REJECT_CONFIRM_POPUP_REQUIRED,
    INSTALL_REJECT_FSR4_ARCHIVE_DOWNLOADING,
    INSTALL_REJECT_FSR4_NOT_READY,
    INSTALL_REJECT_INSTALL_IN_PROGRESS,
    INSTALL_REJECT_INSTALL_PRECHECK_RUNNING,
    INSTALL_REJECT_INVALID_GAME_SELECTION,
    INSTALL_REJECT_MULTI_GPU_BLOCKED,
    INSTALL_REJECT_NO_GAME_SELECTED,
    INSTALL_REJECT_OPTISCALER_ARCHIVE_DOWNLOADING,
    INSTALL_REJECT_OPTISCALER_ARCHIVE_NOT_READY,
    INSTALL_REJECT_PRECHECK_INCOMPLETE,
    INSTALL_REJECT_PREDOWNLOAD_IN_PROGRESS,
)
from .install_runtime_actions import (
    build_selected_game_snapshot_from_runtime,
    should_apply_fsr4_for_game,
    update_install_button_state,
)
from .install_selection_controller import InstallSelectionPrecheckOutcome
from .install_state import build_install_entry_state, resolve_ready_cached_archive_path
from .runtime_state import (
    ArchiveRuntimeState,
    GpuRuntimeState,
    InstallRuntimeState,
    SheetRuntimeState,
)


@dataclass(frozen=True)
class InstallFlowCallbacks:
    get_lang: Callable[[], str]
    should_apply_fsr4_for_game: Callable[[Mapping[str, Any]], bool]
    update_install_button_state: Callable[[], None]
    show_after_install_popup: Callable[[Mapping[str, Any]], None]
    set_information_text: Callable[[str], None]
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
        callbacks: InstallFlowCallbacks,
        create_prefixed_logger: Callable[[str], Any],
    ) -> None:
        self._app_ref = app_ref
        self._root = root
        self._task_executor = task_executor
        self._txt = strings
        self._archive_state = archive_state
        self._gpu_state = gpu_state
        self._sheet_state = sheet_state
        self._install_state = install_state
        self._callbacks = callbacks
        self._create_prefixed_logger = create_prefixed_logger

    def run_install_precheck(self, game_data: Mapping[str, Any]) -> InstallSelectionPrecheckOutcome:
        logger_name = str(game_data.get("game_name_en", "") or game_data.get("display", "unknown")).strip() or "unknown"
        logger = self._create_prefixed_logger(logger_name)
        handler = get_game_handler(game_data)
        self._install_state.precheck_ual_detected_names = ()
        try:
            logger.info("Running install precheck with handler: %s", getattr(handler, "handler_key", "default"))
            use_korean = self._is_korean()
            precheck = handler.run_install_precheck(game_data, use_korean, logger)
            notice_message = handler.format_precheck_notice(precheck, use_korean)
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
                return InstallSelectionPrecheckOutcome(
                    ok=True,
                    resolved_dll_name=resolved_dll_name,
                    mod_notice_message=notice_message,
                )
            formatted_error = handler.format_precheck_error(precheck, use_korean)
            popup_message = handler.get_precheck_popup_message(precheck, use_korean)
            logger.warning("Install precheck failed: %s", precheck.raw_error_message)
            return InstallSelectionPrecheckOutcome(
                ok=False,
                error=formatted_error,
                popup_message=popup_message,
                mod_notice_message=notice_message,
            )
        except Exception as exc:
            logger.error("Install precheck failed unexpectedly: %s", redact_text(exc))
            return InstallSelectionPrecheckOutcome(
                ok=False,
                error=str(exc),
            )

    def build_install_entry_state(self) -> InstallEntryState:
        selection = build_selected_game_snapshot_from_runtime(self._app_ref)
        archive = self._archive_state
        predownload_in_progress = bool(
            archive.optipatcher_downloading
            or archive.specialk_downloading
            or archive.ual_downloading
            or archive.unreal5_downloading
        )
        return build_install_entry_state(
            selection=selection,
            multi_gpu_blocked=self._gpu_state.multi_gpu_blocked,
            install_in_progress=self._install_state.in_progress,
            optiscaler_archive_downloading=archive.optiscaler_downloading,
            install_precheck_running=self._install_state.precheck_running,
            install_precheck_ok=self._install_state.precheck_ok,
            install_precheck_error=self._install_state.precheck_error,
            install_precheck_dll_name=self._install_state.precheck_dll_name,
            optiscaler_archive_ready=archive.optiscaler_ready,
            opti_source_archive=archive.opti_source_archive,
            optiscaler_archive_error=archive.optiscaler_error,
            fsr4_archive_downloading=archive.fsr4_downloading,
            fsr4_archive_ready=archive.fsr4_ready,
            fsr4_source_archive=archive.fsr4_source_archive,
            fsr4_archive_error=archive.fsr4_error,
            game_popup_confirmed=self._install_state.popup_confirmed,
            predownload_in_progress=predownload_in_progress,
            ual_cached_archive=resolve_ready_cached_archive_path(archive.ual_ready, archive.ual_source_archive),
            optipatcher_cached_archive=resolve_ready_cached_archive_path(
                archive.optipatcher_ready,
                archive.optipatcher_source_archive,
            ),
            specialk_cached_archive=resolve_ready_cached_archive_path(
                archive.specialk_ready,
                archive.specialk_source_archive,
            ),
            unreal5_cached_archive=resolve_ready_cached_archive_path(
                archive.unreal5_ready,
                archive.unreal5_source_archive,
            ),
        )

    def show_install_entry_rejection(self, decision: InstallEntryDecision) -> None:
        if decision.code in {
            INSTALL_REJECT_MULTI_GPU_BLOCKED,
            INSTALL_REJECT_INSTALL_PRECHECK_RUNNING,
            INSTALL_REJECT_INSTALL_IN_PROGRESS,
        }:
            return

        if decision.code == INSTALL_REJECT_PREDOWNLOAD_IN_PROGRESS:
            self._callbacks.show_info(self._txt.dialogs.preparing_download_title, self._txt.dialogs.preparing_download_body)
            return

        if decision.code == INSTALL_REJECT_NO_GAME_SELECTED:
            self._callbacks.show_warning(self._txt.common.warning, self._txt.dialogs.select_game_card_body)
            return

        if decision.code == INSTALL_REJECT_OPTISCALER_ARCHIVE_DOWNLOADING:
            self._callbacks.show_info(self._txt.dialogs.preparing_archive_title, self._txt.dialogs.preparing_archive_body)
            return

        if decision.code == INSTALL_REJECT_PRECHECK_INCOMPLETE:
            detail = decision.detail or self._txt.dialogs.precheck_incomplete_body
            detail = f"{detail}\n\n{self._txt.dialogs.precheck_retry_mods_body}"
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == INSTALL_REJECT_OPTISCALER_ARCHIVE_NOT_READY:
            detail = decision.detail or self._txt.dialogs.optiscaler_archive_not_ready
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == INSTALL_REJECT_INVALID_GAME_SELECTION:
            self._callbacks.show_warning(self._txt.common.warning, self._txt.dialogs.invalid_game_body)
            return

        if decision.code == INSTALL_REJECT_FSR4_ARCHIVE_DOWNLOADING:
            self._callbacks.show_info(self._txt.dialogs.preparing_download_title, self._txt.dialogs.preparing_download_body)
            return

        if decision.code == INSTALL_REJECT_FSR4_NOT_READY:
            detail = decision.detail or self._txt.dialogs.fsr4_not_ready
            self._callbacks.show_warning(self._txt.common.warning, detail)
            return

        if decision.code == INSTALL_REJECT_CONFIRM_POPUP_REQUIRED:
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
            self.run_install_worker,
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
        game_name = str(game_data.get("game_name_en", "") or game_data.get("display", "unknown")).strip() or "unknown"
        logger = self._create_prefixed_logger(game_name)
        try:
            ual_detected_names = tuple(self._install_state.precheck_ual_detected_names or ())
            install_ctx = build_install_context(
                self._app_ref,
                game_data,
                source_archive,
                resolved_dll_name,
                fsr4_source_archive,
                fsr4_required,
                ual_detected_names,
                logger,
            )
            installed_game = run_install_workflow(
                self._app_ref,
                install_ctx,
                self._sheet_state.module_download_links,
                create_install_workflow_callbacks(),
                logger,
                ual_cached_archive=ual_cached_archive,
                optipatcher_cached_archive=optipatcher_cached_archive,
                specialk_cached_archive=specialk_cached_archive,
                unreal5_cached_archive=unreal5_cached_archive,
            )
            self._root.after(
                0,
                lambda game=dict(installed_game): self.on_install_finished(True, "Install Completed", game),
            )
        except RuntimeError as exc:
            self._root.after(
                0,
                lambda err=exc, game=dict(game_data): self.on_install_finished(False, str(err), game),
            )
        except Exception as exc:
            logger.error("Install failed: %s", redact_text(exc))
            self._root.after(
                0,
                lambda err=exc, game=dict(game_data): self.on_install_finished(False, str(err), game),
            )

    def on_install_finished(self, success: bool, message: str, installed_game=None) -> None:
        self._install_state.in_progress = False
        self._callbacks.update_install_button_state()

        if success:
            game = installed_game if isinstance(installed_game, dict) else {}
            self._callbacks.set_information_text(
                build_install_information_text(
                    game,
                    lang=self._callbacks.get_lang(),
                )
            )
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
    create_prefixed_logger: Callable[[str], Any],
) -> InstallFlowController:
    def _show_after_install_popup(game: Mapping[str, Any]) -> None:
        ui_shell = getattr(app, "_ui_shell", None)
        if ui_shell is None:
            return
        ui_shell.show_after_install_popup(game)

    return InstallFlowController(
        app_ref=app,
        root=app.root,
        task_executor=app._task_executor,
        strings=app.txt,
        archive_state=app.archive_state,
        gpu_state=app.gpu_state,
        sheet_state=app.sheet_state,
        install_state=app.install_state,
        callbacks=InstallFlowCallbacks(
            get_lang=lambda: app.lang,
            should_apply_fsr4_for_game=lambda game_data: should_apply_fsr4_for_game(app, game_data),
            update_install_button_state=lambda: update_install_button_state(app),
            show_after_install_popup=_show_after_install_popup,
            set_information_text=lambda text="": set_information_text(app, text),
            show_info=messagebox.showinfo,
            show_warning=messagebox.showwarning,
            show_error=messagebox.showerror,
        ),
        create_prefixed_logger=create_prefixed_logger,
    )


__all__ = [
    "InstallFlowCallbacks",
    "InstallFlowController",
    "create_install_flow_controller",
]
