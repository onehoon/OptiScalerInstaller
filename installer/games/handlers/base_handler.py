from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...i18n import lang_from_bool, pick_sheet_text, translate_default_precheck_error
from ...install import services as installer_services

from .install_precheck import build_mod_conflict_notice, scan_target_mod_conflicts


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
    error_message: str = ""
    notice_message: str = ""


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

    def run_install_precheck(
        self,
        game_data: Mapping[str, Any],
        use_korean: bool,
        logger,
    ) -> InstallPrecheckResult:
        target_path = str(game_data.get("path", "")).strip()
        preferred_dll = str(game_data.get("dll_name", "")).strip()
        conflict_findings = scan_target_mod_conflicts(target_path, logger=logger)
        notice_message = build_mod_conflict_notice(conflict_findings, use_korean)
        try:
            resolved_name = installer_services.resolve_proxy_dll_name(target_path, preferred_dll, logger=logger)
            return InstallPrecheckResult(
                ok=True,
                resolved_dll_name=resolved_name,
                notice_message=notice_message,
            )
        except Exception as exc:
            return InstallPrecheckResult(
                ok=False,
                error_message=_translate_default_precheck_error(str(exc), use_korean),
                notice_message=notice_message,
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
