from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...i18n import get_app_strings, lang_from_bool

from .base_handler import BaseGameHandler, InstallPrecheckResult


EXPECTED_RDR2_EXE_NAME = "rdr2.exe"
EXPECTED_RDR2_GAME_NAME = "Red Dead Redemption 2"


@dataclass(frozen=True)
class Rdr2BlockedModRule:
    name: str
    patterns: tuple[str, ...]


RDR2_BLOCKED_MOD_RULES: tuple[Rdr2BlockedModRule, ...] = (
    Rdr2BlockedModRule(
        name="ScriptHookRDR2",
        patterns=(
            "scripthookrdr2.dll",
            "scripthookrdr2.log",
        ),
    ),
    Rdr2BlockedModRule(
        name="Lenny's Mod Loader RDR2",
        patterns=(
            "vfs.asi",
            "lml.ini",
        ),
    ),
)


def _normalize_file_relpath(path: Path, base_dir: Path) -> str:
    return str(path.relative_to(base_dir)).replace("\\", "/")


def _scan_rdr2_blocked_mods(target_path: str, logger=None) -> tuple[str, ...]:
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir() or not RDR2_BLOCKED_MOD_RULES:
        return ()

    try:
        file_entries = [
            (
                file_path.name.lower(),
                _normalize_file_relpath(file_path, target_dir).lower(),
            )
            for file_path in target_dir.rglob("*")
            if file_path.is_file()
        ]
    except Exception:
        if logger:
            logger.exception("Failed to scan RDR2 blocked mods in %s", target_dir)
        return ()

    detected: list[str] = []
    for rule in RDR2_BLOCKED_MOD_RULES:
        if any(
            fnmatch.fnmatch(file_name, pattern.lower()) or fnmatch.fnmatch(rel_path, pattern.lower())
            for file_name, rel_path in file_entries
            for pattern in rule.patterns
        ):
            detected.append(rule.name)

    return tuple(dict.fromkeys(detected))


def _build_rdr2_blocked_mod_error(detected_mods: tuple[str, ...], use_korean: bool) -> str:
    detected_text = ", ".join(detected_mods)
    if lang_from_bool(use_korean) == "ko":
        return (
            "RDR2 \uc124\uce58\ub97c \uc9c4\ud589\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4. "
            f"\ud638\ud658\ub418\uc9c0 \uc54a\ub294 MOD\uac00 \uac10\uc9c0\ub418\uc5c8\uc2b5\ub2c8\ub2e4: {detected_text}"
        )
    return f"RDR2 installation cannot continue because incompatible mods were detected: {detected_text}"


def _build_rdr2_blocked_mod_popup(detected_mods: tuple[str, ...], use_korean: bool) -> str:
    normalized_mods = tuple(str(mod).strip() for mod in detected_mods if str(mod).strip())
    if not normalized_mods:
        return ""

    strings = get_app_strings(lang_from_bool(use_korean))
    mods_markup = "[BR]".join(f"[INDENT]{mod_name}" for mod_name in normalized_mods)
    return strings.precheck.rdr2_blocked_mod_popup_template.format(mods=mods_markup)


class Rdr2Handler(BaseGameHandler):
    """RDR2-specific hard-stop precheck for blocked third-party mods."""

    handler_key = "rdr2"
    aliases = ()

    def matches(self, game_data: Mapping[str, Any]) -> bool:
        game_name = str(game_data.get("game_name_en", "") or "").strip().lower()
        exe_name = Path(str(game_data.get("exe", "") or game_data.get("exe_path", "") or "")).name.strip().lower()
        return game_name == EXPECTED_RDR2_GAME_NAME.lower() and exe_name == EXPECTED_RDR2_EXE_NAME

    def run_install_precheck(
        self,
        game_data: Mapping[str, Any],
        use_korean: bool,
        logger,
    ) -> InstallPrecheckResult:
        base_precheck = super().run_install_precheck(game_data, use_korean, logger)
        if not base_precheck.ok:
            return base_precheck

        target_path = str(game_data.get("path", "")).strip()
        blocked_mods = _scan_rdr2_blocked_mods(target_path, logger=logger)
        if blocked_mods:
            return InstallPrecheckResult(
                ok=False,
                raw_error_message=_build_rdr2_blocked_mod_error(blocked_mods, False),
                conflict_findings=base_precheck.conflict_findings,
                error_code="blocked_mods",
                error_context={"detected_mods": blocked_mods},
            )

        return base_precheck

    def format_precheck_error(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        if precheck.error_code == "blocked_mods":
            blocked_mods = tuple(
                str(mod).strip()
                for mod in precheck.error_context.get("detected_mods", ())
                if str(mod).strip()
            )
            return _build_rdr2_blocked_mod_error(blocked_mods, use_korean)
        return super().format_precheck_error(precheck, use_korean)

    def get_precheck_popup_message(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        if precheck.error_code != "blocked_mods":
            return ""
        blocked_mods = tuple(
            str(mod).strip()
            for mod in precheck.error_context.get("detected_mods", ())
            if str(mod).strip()
        )
        return _build_rdr2_blocked_mod_popup(blocked_mods, use_korean)
