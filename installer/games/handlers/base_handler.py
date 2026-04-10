from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ...i18n import lang_from_bool, pick_sheet_text, translate_default_precheck_error
from ...install import services as installer_services

from .install_precheck import (
    ModConflictFinding,
    RESHADE_INSTALL_MODE_DISABLED,
    RESHADE_INSTALL_MODE_INVALID_MULTIPLE,
    SPECIALK_INSTALL_MODE_DISABLED,
    build_mod_conflict_findings,
    build_mod_conflict_notice,
    build_reshade_install_error,
    resolve_reshade_install_state,
    resolve_specialk_install_state,
    scan_mod_precheck_state,
)


def _normalize_handler_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _iter_game_tokens(game_data: Mapping[str, Any]) -> Iterable[str]:
    for key in ("game_name", "display", "exe", "exe_path"):
        normalized = _normalize_handler_token(game_data.get(key, ""))
        if normalized:
            yield normalized

    for token in game_data.get("match_files") or ():
        normalized = _normalize_handler_token(token)
        if normalized:
            yield normalized


def _translate_default_precheck_error(raw_error: str, use_korean: bool) -> str:
    return translate_default_precheck_error(raw_error, lang_from_bool(use_korean))


@dataclass(frozen=True)
class InstallPrecheckResult:
    ok: bool
    resolved_dll_name: str = ""
    raw_error_message: str = ""
    conflict_findings: tuple[ModConflictFinding, ...] = ()
    error_code: str = ""
    error_context: Mapping[str, Any] = field(default_factory=dict)
    reshade_install_mode: str = RESHADE_INSTALL_MODE_DISABLED
    reshade_source_dll_name: str = ""
    specialk_install_mode: str = SPECIALK_INSTALL_MODE_DISABLED
    specialk_source_dll_name: str = ""


@dataclass(frozen=True)
class InstallPlan:
    game_data: dict[str, Any]
    source_archive: str
    resolved_dll_name: str


class GameHandlerCancelled(Exception):
    """Raised when a game-specific handler intentionally cancels installation."""


class BaseGameHandler:
    handler_key = "default"
    aliases: tuple[str, ...] = ()

    def matches(self, game_data: Mapping[str, Any]) -> bool:
        expected = {
            normalized
            for normalized in (
                _normalize_handler_token(self.handler_key),
                *(_normalize_handler_token(alias) for alias in self.aliases),
            )
            if normalized
        }
        if not expected:
            return False
        return any(token in expected for token in _iter_game_tokens(game_data))

    def get_selection_popup_message(self, game_data: Mapping[str, Any], use_korean: bool) -> str:
        return pick_sheet_text(game_data, "popup", lang_from_bool(use_korean))

    def get_after_install_popup_message(self, game_data: Mapping[str, Any], use_korean: bool) -> str:
        return pick_sheet_text(game_data, "after_popup", lang_from_bool(use_korean))

    def get_after_install_guide_url(self, game_data: Mapping[str, Any]) -> str:
        return str(game_data.get("guidepage_after_installation", "") or "").strip()

    def format_precheck_notice(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        return build_mod_conflict_notice(precheck.conflict_findings, use_korean)

    def format_precheck_error(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        if precheck.error_code == "reshade_invalid_multiple":
            return build_reshade_install_error(
                precheck.error_context.get("detected_dll_names", ()),
                use_korean,
            )
        return _translate_default_precheck_error(precheck.raw_error_message, use_korean)

    def get_precheck_popup_message(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        return ""

    def run_install_precheck(
        self,
        game_data: Mapping[str, Any],
        use_korean: bool,
        logger,
    ) -> InstallPrecheckResult:
        target_path = str(game_data.get("path", "")).strip()
        preferred_dll = str(game_data.get("dll_name", "")).strip()
        mod_state = scan_mod_precheck_state(target_path, logger=logger)
        conflict_findings = build_mod_conflict_findings(mod_state)
        reshade_state = resolve_reshade_install_state(mod_state)
        specialk_state = resolve_specialk_install_state(mod_state)
        if reshade_state.mode == RESHADE_INSTALL_MODE_INVALID_MULTIPLE:
            return InstallPrecheckResult(
                ok=False,
                raw_error_message=build_reshade_install_error(
                    reshade_state.detected_dll_names,
                    False,
                ),
                conflict_findings=conflict_findings,
                error_code="reshade_invalid_multiple",
                error_context={"detected_dll_names": reshade_state.detected_dll_names},
                reshade_install_mode=reshade_state.mode,
                reshade_source_dll_name=reshade_state.source_dll_name,
                specialk_install_mode=specialk_state.mode,
                specialk_source_dll_name=specialk_state.source_dll_name,
            )
        try:
            reusable_filenames = []
            if reshade_state.source_dll_name:
                reusable_filenames.append(reshade_state.source_dll_name)
            if specialk_state.source_dll_name:
                reusable_filenames.append(specialk_state.source_dll_name)
            resolved_name = installer_services.resolve_proxy_dll_name(
                target_path,
                preferred_dll,
                logger=logger,
                reusable_filenames=tuple(reusable_filenames),
            )
            return InstallPrecheckResult(
                ok=True,
                resolved_dll_name=resolved_name,
                conflict_findings=conflict_findings,
                reshade_install_mode=reshade_state.mode,
                reshade_source_dll_name=reshade_state.source_dll_name,
                specialk_install_mode=specialk_state.mode,
                specialk_source_dll_name=specialk_state.source_dll_name,
            )
        except Exception as exc:
            return InstallPrecheckResult(
                ok=False,
                raw_error_message=str(exc),
                conflict_findings=conflict_findings,
                reshade_install_mode=reshade_state.mode,
                reshade_source_dll_name=reshade_state.source_dll_name,
                specialk_install_mode=specialk_state.mode,
                specialk_source_dll_name=specialk_state.source_dll_name,
            )

    def prepare_install_plan(
        self,
        app: Any,
        game_data: Mapping[str, Any],
        source_archive: str,
        resolved_dll_name: str,
        logger,
    ) -> InstallPlan:
        return InstallPlan(
            game_data=dict(game_data),
            source_archive=str(source_archive or ""),
            resolved_dll_name=str(resolved_dll_name or ""),
        )

    def finalize_install(
        self,
        app: Any,
        game_data: Mapping[str, Any],
        target_path: str,
        logger,
    ) -> None:
        """Hook for game-specific install steps after the shared install flow completes."""
