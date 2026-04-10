from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path
from typing import Iterable

from ...i18n import build_mod_conflict_finding_text, build_mod_conflict_notice_text, lang_from_bool
from ...install import services as installer_services

_MONITORED_DLL_NAMES = (
    "dxgi.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3d10.dll",
    "d3d9.dll",
    "dinput8.dll",
    "reshade64.dll",
    "specialk64.dll",
    "specialk32.dll",
    "version.dll",
    "winmm.dll",
)
_MONITORED_DLL_NAME_SET = {name.lower() for name in _MONITORED_DLL_NAMES}

_RENODX_ADDON_PATTERN = "renodx*.addon"
_RENODX_ADDONS_RELATIVE_DIR = Path("reshade-shaders") / "Addons"

_OWNER_KEYWORDS = {
    "reshade": ("reshade",),
    "special_k": ("special k", "specialk"),
    "ultimate_asi_loader": ("ultimate asi loader",),
}
RESHADE_COMPAT_DLL_NAME = "ReShade64.dll"
RESHADE_INSTALL_MODE_DISABLED = "disabled"
RESHADE_INSTALL_MODE_MIGRATE = "migrate"
RESHADE_INSTALL_MODE_ALREADY_MIGRATED = "already_migrated"
RESHADE_INSTALL_MODE_INVALID_MULTIPLE = "invalid_multiple"
SPECIALK_INSTALL_MODE_DISABLED = "disabled"
SPECIALK_INSTALL_MODE_MIGRATE = "migrate"
_SPECIALK_SOURCE_PRIORITY = (
    "dxgi.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3d10.dll",
    "d3d9.dll",
    "version.dll",
    "winmm.dll",
    "dinput8.dll",
    "specialk64.dll",
    "specialk32.dll",
)


@dataclass(frozen=True)
class ModBinaryState:
    detected: bool = False
    dll_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenoDxState:
    detected: bool = False
    addon_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModPrecheckState:
    reshade: ModBinaryState
    special_k: ModBinaryState
    ultimate_asi_loader: ModBinaryState
    renodx: RenoDxState

    @property
    def any_detected(self) -> bool:
        return any(
            (
                self.reshade.detected,
                self.special_k.detected,
                self.ultimate_asi_loader.detected,
                self.renodx.detected,
            )
        )


@dataclass(frozen=True)
class ModConflictFinding:
    kind: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ReShadeInstallState:
    mode: str = RESHADE_INSTALL_MODE_DISABLED
    source_dll_name: str = ""
    detected_dll_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecialKInstallState:
    mode: str = SPECIALK_INSTALL_MODE_DISABLED
    source_dll_name: str = ""
    detected_dll_names: tuple[str, ...] = ()


def _empty_mod_binary_state() -> ModBinaryState:
    return ModBinaryState(detected=False, dll_names=())


def _empty_renodx_state() -> RenoDxState:
    return RenoDxState(detected=False, addon_paths=())


def empty_mod_precheck_state() -> ModPrecheckState:
    return ModPrecheckState(
        reshade=_empty_mod_binary_state(),
        special_k=_empty_mod_binary_state(),
        ultimate_asi_loader=_empty_mod_binary_state(),
        renodx=_empty_renodx_state(),
    )


def _normalize_unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    unique_values = {str(value).strip() for value in values if str(value).strip()}
    return tuple(sorted(unique_values, key=str.lower))


def _scan_candidate_dlls(target_dir: Path, logger=None) -> dict[str, Path]:
    dll_files: dict[str, Path] = {}
    try:
        for child in target_dir.iterdir():
            try:
                if not child.is_file():
                    continue
            except Exception:
                if logger:
                    logger.debug("Failed to inspect candidate file in %s", target_dir, exc_info=True)
                continue

            lowered_name = child.name.lower()
            if lowered_name not in _MONITORED_DLL_NAME_SET:
                continue
            dll_files.setdefault(lowered_name, child)
    except Exception:
        if logger:
            logger.debug("Failed to scan DLL candidates in %s", target_dir, exc_info=True)
    return dll_files


def _scan_renodx_addons(target_dir: Path, logger=None) -> tuple[str, ...]:
    hits: list[str] = []
    search_dirs = (
        target_dir,
        target_dir / _RENODX_ADDONS_RELATIVE_DIR,
    )
    for search_dir in search_dirs:
        try:
            if not search_dir.is_dir():
                continue
            for child in search_dir.iterdir():
                try:
                    if not child.is_file():
                        continue
                except Exception:
                    if logger:
                        logger.debug("Failed to inspect RenoDX addon candidate in %s", search_dir, exc_info=True)
                    continue

                lowered_name = child.name.lower()
                if not fnmatch.fnmatch(lowered_name, _RENODX_ADDON_PATTERN):
                    continue
                hits.append(str(child.relative_to(target_dir)).replace("\\", "/"))
        except Exception:
            if logger:
                logger.debug("Failed to scan RenoDX addons in %s", search_dir, exc_info=True)
    return _normalize_unique_strings(hits)


def _is_optiscaler_managed(file_path: Path) -> bool:
    try:
        return bool(installer_services.is_optiscaler_managed_proxy_dll(file_path))
    except Exception:
        return False


def _identify_binary_owner(file_path: Path) -> str:
    if _is_optiscaler_managed(file_path):
        return "optiscaler"

    version_info = installer_services.read_windows_version_strings(file_path)
    haystack = " ".join(
        part.lower()
        for part in [file_path.name, *(str(value) for value in version_info.values())]
        if str(part or "").strip()
    )
    for owner, keywords in _OWNER_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return owner
    return ""


def _build_mod_binary_state(paths: Iterable[Path]) -> ModBinaryState:
    dll_names = _normalize_unique_strings(path.name for path in paths)
    return ModBinaryState(detected=bool(dll_names), dll_names=dll_names)


def _build_renodx_state(addon_paths: Iterable[str]) -> RenoDxState:
    normalized_paths = _normalize_unique_strings(addon_paths)
    return RenoDxState(detected=bool(normalized_paths), addon_paths=normalized_paths)


def scan_mod_precheck_state(target_path: str, logger=None) -> ModPrecheckState:
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        return empty_mod_precheck_state()

    dll_files = _scan_candidate_dlls(target_dir, logger=logger)
    detected_paths: dict[str, list[Path]] = {
        "reshade": [],
        "special_k": [],
        "ultimate_asi_loader": [],
    }

    for file_path in dll_files.values():
        owner = _identify_binary_owner(file_path)
        if owner in detected_paths:
            detected_paths[owner].append(file_path)

    renodx_paths = _scan_renodx_addons(target_dir, logger=logger)
    return ModPrecheckState(
        reshade=_build_mod_binary_state(detected_paths["reshade"]),
        special_k=_build_mod_binary_state(detected_paths["special_k"]),
        ultimate_asi_loader=_build_mod_binary_state(detected_paths["ultimate_asi_loader"]),
        renodx=_build_renodx_state(renodx_paths),
    )


def _build_finding(kind: str, evidence: Iterable[str]) -> ModConflictFinding | None:
    normalized = _normalize_unique_strings(evidence)
    if not normalized:
        return None
    return ModConflictFinding(kind=kind, evidence=normalized)


def build_mod_conflict_findings(state: ModPrecheckState) -> tuple[ModConflictFinding, ...]:
    findings: list[ModConflictFinding] = []

    reshade_finding = _build_finding("reshade", state.reshade.dll_names)
    if reshade_finding:
        findings.append(reshade_finding)

    special_k_finding = _build_finding("special_k", state.special_k.dll_names)
    if special_k_finding:
        findings.append(special_k_finding)

    asi_loader_finding = _build_finding("ultimate_asi_loader", state.ultimate_asi_loader.dll_names)
    if asi_loader_finding:
        findings.append(asi_loader_finding)

    renodx_finding = _build_finding("renodx", state.renodx.addon_paths)
    if renodx_finding:
        findings.append(renodx_finding)

    return tuple(findings)


def scan_target_mod_conflicts(target_path: str, logger=None) -> tuple[ModConflictFinding, ...]:
    state = scan_mod_precheck_state(target_path, logger=logger)
    return build_mod_conflict_findings(state)


def resolve_reshade_install_state(state: ModPrecheckState) -> ReShadeInstallState:
    if not installer_services.RESHADE_COMPAT_INSTALL_ENABLED:
        return ReShadeInstallState()

    detected_names = tuple(str(name).strip() for name in state.reshade.dll_names if str(name).strip())
    if not detected_names:
        return ReShadeInstallState()

    normalized_names = {name.lower() for name in detected_names}
    compat_name = RESHADE_COMPAT_DLL_NAME.lower()
    if len(normalized_names) == 1 and compat_name in normalized_names:
        return ReShadeInstallState(
            mode=RESHADE_INSTALL_MODE_ALREADY_MIGRATED,
            detected_dll_names=detected_names,
        )

    if len(normalized_names) > 1 or compat_name in normalized_names:
        return ReShadeInstallState(
            mode=RESHADE_INSTALL_MODE_INVALID_MULTIPLE,
            detected_dll_names=detected_names,
        )

    return ReShadeInstallState(
        mode=RESHADE_INSTALL_MODE_MIGRATE,
        source_dll_name=detected_names[0],
        detected_dll_names=detected_names,
    )


def resolve_specialk_install_state(state: ModPrecheckState) -> SpecialKInstallState:
    if not installer_services.SPECIALK_AUTO_DETECT_INSTALL_ENABLED:
        return SpecialKInstallState()

    detected_names = tuple(str(name).strip() for name in state.special_k.dll_names if str(name).strip())
    if not detected_names:
        return SpecialKInstallState()

    normalized_by_name = {name.lower(): name for name in detected_names}
    for preferred_name in _SPECIALK_SOURCE_PRIORITY:
        if preferred_name in normalized_by_name:
            return SpecialKInstallState(
                mode=SPECIALK_INSTALL_MODE_MIGRATE,
                source_dll_name=normalized_by_name[preferred_name],
                detected_dll_names=detected_names,
            )

    return SpecialKInstallState(
        mode=SPECIALK_INSTALL_MODE_MIGRATE,
        source_dll_name=sorted(detected_names, key=str.lower)[0],
        detected_dll_names=detected_names,
    )


def build_reshade_install_error(detected_dll_names: Iterable[str], use_korean: bool) -> str:
    detected = ", ".join(_normalize_unique_strings(detected_dll_names))
    if lang_from_bool(use_korean) == "ko":
        return (
            "ReShade DLL이 여러 개 감지되어 설치를 진행할 수 없습니다. "
            f"하나의 ReShade만 남기고 다시 시도해 주세요: {detected}"
        )
    return (
        "Installation cannot continue because multiple ReShade DLLs were detected. "
        f"Leave only one ReShade hook DLL and try again: {detected}"
    )


def _format_finding(finding: ModConflictFinding, use_korean: bool) -> str:
    detected = ", ".join(finding.evidence)
    return build_mod_conflict_finding_text(finding.kind, detected, lang_from_bool(use_korean))


def build_mod_conflict_notice(findings: Iterable[ModConflictFinding], use_korean: bool) -> str:
    normalized_findings = tuple(findings)
    if not normalized_findings:
        return ""

    # Ultimate ASI Loader-only detection is treated as informational and does not
    # block or warn in the MOD popup flow.
    if all(finding.kind == "ultimate_asi_loader" for finding in normalized_findings):
        return ""

    lines = [_format_finding(finding, use_korean) for finding in normalized_findings]
    return build_mod_conflict_notice_text(lines, lang_from_bool(use_korean))


__all__ = [
    "ModBinaryState",
    "ModConflictFinding",
    "ModPrecheckState",
    "RESHADE_COMPAT_DLL_NAME",
    "RESHADE_INSTALL_MODE_ALREADY_MIGRATED",
    "RESHADE_INSTALL_MODE_DISABLED",
    "RESHADE_INSTALL_MODE_INVALID_MULTIPLE",
    "RESHADE_INSTALL_MODE_MIGRATE",
    "SPECIALK_INSTALL_MODE_DISABLED",
    "SPECIALK_INSTALL_MODE_MIGRATE",
    "ReShadeInstallState",
    "RenoDxState",
    "SpecialKInstallState",
    "build_mod_conflict_findings",
    "build_mod_conflict_notice",
    "build_reshade_install_error",
    "empty_mod_precheck_state",
    "resolve_reshade_install_state",
    "resolve_specialk_install_state",
    "scan_mod_precheck_state",
    "scan_target_mod_conflicts",
]
