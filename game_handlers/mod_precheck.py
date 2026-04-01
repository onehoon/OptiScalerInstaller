from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path
from typing import Iterable

import installer_services

_MONITORED_DLL_NAMES = (
    "dxgi.dll",
    "d3d12.dll",
    "d3d11.dll",
    "d3d10.dll",
    "d3d9.dll",
    "dinput8.dll",
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
        return bool(installer_services._is_optiscaler_managed_proxy_dll(file_path))
    except Exception:
        return False


def _identify_binary_owner(file_path: Path) -> str:
    if _is_optiscaler_managed(file_path):
        return "optiscaler"

    version_reader = getattr(installer_services, "_read_windows_version_strings", None)
    version_info = version_reader(file_path) if callable(version_reader) else {}
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


def scan_target_mod_conflicts(target_path: str, logger=None) -> tuple[ModConflictFinding, ...]:
    state = scan_mod_precheck_state(target_path, logger=logger)
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


def _format_finding(finding: ModConflictFinding, use_korean: bool) -> str:
    detected = ", ".join(finding.evidence)
    if finding.kind == "reshade":
        return (
            f"ReShade related files were detected: {detected}"
            if not use_korean
            else f"ReShade related files were detected: {detected}"
        )
    if finding.kind == "special_k":
        return (
            f"Special K related files were detected: {detected}"
            if not use_korean
            else f"Special K related files were detected: {detected}"
        )
    if finding.kind == "ultimate_asi_loader":
        return (
            f"Ultimate ASI Loader related files were detected: {detected}"
            if not use_korean
            else f"Ultimate ASI Loader related files were detected: {detected}"
        )
    if finding.kind == "renodx":
        return (
            f"RenoDX addon files were detected: {detected}"
            if not use_korean
            else f"RenoDX addon files were detected: {detected}"
        )
    return (
        f"MOD-related files were detected: {detected}"
        if not use_korean
        else f"MOD-related files were detected: {detected}"
    )


def build_mod_conflict_notice(findings: Iterable[ModConflictFinding], use_korean: bool) -> str:
    normalized_findings = tuple(findings)
    if not normalized_findings:
        return ""

    header = (
        "Existing MOD files were detected. Please review the current DLL setup before installing."
        if not use_korean
        else "Existing MOD files were detected. Please review the current DLL setup before installing."
    )
    footer = (
        "This is a safety notice. DLL-based mods that share proxy names can conflict with installation or runtime behavior."
        if not use_korean
        else "This is a safety notice. DLL-based mods that share proxy names can conflict with installation or runtime behavior."
    )

    lines = [header, ""]
    for finding in normalized_findings:
        lines.append(f"- {_format_finding(finding, use_korean)}")
    lines.extend(("", footer))
    return "\n".join(lines).strip()


__all__ = [
    "ModBinaryState",
    "ModConflictFinding",
    "ModPrecheckState",
    "RenoDxState",
    "build_mod_conflict_notice",
    "empty_mod_precheck_state",
    "scan_mod_precheck_state",
    "scan_target_mod_conflicts",
]
