from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...i18n import get_app_strings, lang_from_bool
from ...install import OPTISCALER_ASI_NAME

from .base_handler import BaseGameHandler, InstallPlan, InstallPrecheckResult
from .install_precheck import (
    RESHADE_INSTALL_MODE_INVALID_MULTIPLE,
    build_mod_conflict_findings,
    build_reshade_install_error,
    resolve_reshade_install_state,
    resolve_specialk_install_state,
    scan_mod_precheck_state,
)
from .rdr2_xml import apply_rdr2_system_xml_settings, resolve_rdr2_system_xml_path


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
        return f"RDR2 설치를 진행할 수 없습니다. 호환되지 않는 MOD가 감지되었습니다: {detected_text}"
    return f"RDR2 installation cannot continue because incompatible mods were detected: {detected_text}"


def _build_rdr2_blocked_mod_popup(detected_mods: tuple[str, ...], use_korean: bool) -> str:
    normalized_mods = tuple(str(mod).strip() for mod in detected_mods if str(mod).strip())
    if not normalized_mods:
        return ""

    strings = get_app_strings(lang_from_bool(use_korean))
    mods_markup = "[BR]".join(f"[INDENT]{mod_name}" for mod_name in normalized_mods)
    return strings.precheck.rdr2_blocked_mod_popup_template.format(mods=mods_markup)


def _build_rdr2_missing_xml_error(xml_path: Path, use_korean: bool) -> str:
    if lang_from_bool(use_korean) == "ko":
        return (
            "RDR2 system.xml 파일을 찾을 수 없어 설치를 진행할 수 없습니다. "
            f"게임을 한 번 실행해 설정 파일을 생성한 뒤 다시 시도해 주세요: {xml_path}"
        )
    return (
        "RDR2 installation cannot continue because system.xml was not found. "
        f"Launch the game once so it creates the settings file, then try again: {xml_path}"
    )


class Rdr2Handler(BaseGameHandler):
    """Dedicated hook surface for Red Dead Redemption 2 specific install logic."""

    handler_key = "rdr2"
    aliases = ()

    def matches(self, game_data: Mapping[str, Any]) -> bool:
        game_name = str(game_data.get("game_name", "") or "").strip().lower()
        exe_name = Path(str(game_data.get("exe", "") or game_data.get("exe_path", "") or "")).name.strip().lower()
        return game_name == EXPECTED_RDR2_GAME_NAME.lower() and exe_name == EXPECTED_RDR2_EXE_NAME

    def run_install_precheck(
        self,
        game_data: Mapping[str, Any],
        use_korean: bool,
        logger,
    ) -> InstallPrecheckResult:
        target_path = str(game_data.get("path", "")).strip()
        mod_state = scan_mod_precheck_state(target_path, logger=logger)
        conflict_findings = build_mod_conflict_findings(mod_state)
        reshade_state = resolve_reshade_install_state(mod_state)
        specialk_state = resolve_specialk_install_state(mod_state)
        xml_path = resolve_rdr2_system_xml_path()

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

        if not xml_path.is_file():
            return InstallPrecheckResult(
                ok=False,
                raw_error_message=_build_rdr2_missing_xml_error(xml_path, False),
                conflict_findings=conflict_findings,
                error_code="missing_system_xml",
                error_context={"xml_path": str(xml_path)},
                reshade_install_mode=reshade_state.mode,
                reshade_source_dll_name=reshade_state.source_dll_name,
                specialk_install_mode=specialk_state.mode,
                specialk_source_dll_name=specialk_state.source_dll_name,
            )

        blocked_mods = _scan_rdr2_blocked_mods(target_path, logger=logger)
        if blocked_mods:
            return InstallPrecheckResult(
                ok=False,
                raw_error_message=_build_rdr2_blocked_mod_error(blocked_mods, False),
                conflict_findings=conflict_findings,
                error_code="blocked_mods",
                error_context={"detected_mods": blocked_mods},
                reshade_install_mode=reshade_state.mode,
                reshade_source_dll_name=reshade_state.source_dll_name,
                specialk_install_mode=specialk_state.mode,
                specialk_source_dll_name=specialk_state.source_dll_name,
            )

        return InstallPrecheckResult(
            ok=True,
            resolved_dll_name=OPTISCALER_ASI_NAME,
            conflict_findings=conflict_findings,
            reshade_install_mode=reshade_state.mode,
            reshade_source_dll_name=reshade_state.source_dll_name,
            specialk_install_mode=specialk_state.mode,
            specialk_source_dll_name=specialk_state.source_dll_name,
        )

    def format_precheck_error(self, precheck: InstallPrecheckResult, use_korean: bool) -> str:
        if precheck.error_code == "reshade_invalid_multiple":
            return build_reshade_install_error(
                precheck.error_context.get("detected_dll_names", ()),
                use_korean,
            )
        if precheck.error_code == "missing_system_xml":
            xml_path = Path(str(precheck.error_context.get("xml_path", "")).strip())
            return _build_rdr2_missing_xml_error(xml_path, use_korean)
        if precheck.error_code == "blocked_mods":
            blocked_mods = tuple(str(mod).strip() for mod in precheck.error_context.get("detected_mods", ()) if str(mod).strip())
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

    def prepare_install_plan(
        self,
        app: Any,
        game_data: Mapping[str, Any],
        source_archive: str,
        resolved_dll_name: str,
        logger,
    ) -> InstallPlan:
        plan_game_data = dict(game_data)
        plan_game_data["ultimate_asi_loader"] = True
        plan_game_data["dll_name"] = OPTISCALER_ASI_NAME
        if logger:
            logger.info(
                "RDR2 handler forcing Ultimate ASI Loader and OptiScaler filename: %s",
                OPTISCALER_ASI_NAME,
            )
        return InstallPlan(
            game_data=plan_game_data,
            source_archive=str(source_archive or ""),
            resolved_dll_name=OPTISCALER_ASI_NAME,
        )

    def finalize_install(
        self,
        app: Any,
        game_data: Mapping[str, Any],
        target_path: str,
        logger,
    ) -> None:
        xml_path = apply_rdr2_system_xml_settings(logger=logger)
        if logger:
            logger.info("RDR2 handler finished XML update: %s", xml_path)

