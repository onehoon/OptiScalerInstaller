from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..common.windows_paths import normalize_candidate_path


_SCAN_HINTS_SCHEMA_VERSION = 1
_SCAN_HINTS_FILE_NAME = "manual_scan_hints.json"


def _get_app_cache_dir() -> Path:
    local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        return Path(local_appdata) / "OptiScalerInstaller"
    return Path(tempfile.gettempdir()) / "OptiScalerInstaller"


def _get_manual_scan_hints_path() -> Path:
    return _get_app_cache_dir() / _SCAN_HINTS_FILE_NAME


def _coerce_root_path(raw_path: str) -> Path | None:
    normalized = str(raw_path or "").strip()
    if not normalized:
        return None
    if len(normalized) == 2 and normalized[1] == ":":
        normalized += "\\"
    return Path(normalized)


def _iter_excluded_exact_paths() -> tuple[Path, ...]:
    candidates: list[Path] = []

    system_root = str(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "").strip()
    if system_root:
        candidates.append(Path(system_root))

    system_drive_root = _coerce_root_path(str(os.environ.get("SystemDrive") or "").strip())
    if system_drive_root is not None:
        candidates.append(system_drive_root / "Users")

    userprofile = str(os.environ.get("USERPROFILE") or "").strip()
    if userprofile:
        userprofile_path = Path(userprofile)
        candidates.extend(
            (
                userprofile_path,
                userprofile_path / "AppData" / "LocalLow",
                userprofile_path / "Desktop",
                userprofile_path / "Documents",
                userprofile_path / "Downloads",
            )
        )

    for env_name in ("PUBLIC", "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "APPDATA", "LOCALAPPDATA"):
        env_value = str(os.environ.get(env_name) or "").strip()
        if env_value:
            candidates.append(Path(env_value))

    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        env_value = str(os.environ.get(env_name) or "").strip()
        if not env_value:
            continue
        root_path = Path(env_value)
        candidates.extend(
            (
                root_path,
                root_path / "Desktop",
                root_path / "Documents",
                root_path / "Downloads",
            )
        )

    unique_candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        normalized = normalize_candidate_path(candidate)
        if normalized in seen_candidates:
            continue
        seen_candidates.add(normalized)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


def _is_drive_root(candidate: Path) -> bool:
    anchor = str(candidate.anchor or "").strip()
    if not anchor:
        return False
    return normalize_candidate_path(candidate) == normalize_candidate_path(Path(anchor))


def is_manual_scan_hint_path_allowed(path: str | Path) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    if not candidate.exists() or not candidate.is_dir():
        return False
    if _is_drive_root(candidate):
        return False

    normalized_candidate = normalize_candidate_path(candidate)
    excluded_paths = {normalize_candidate_path(excluded) for excluded in _iter_excluded_exact_paths()}
    return normalized_candidate not in excluded_paths


def _load_scan_hint_payload(path: Path, *, logger=None) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger:
            logger.debug("Failed to read manual scan hints from %s: %s", path, exc)
        return {}

    return payload if isinstance(payload, dict) else {}


def load_manual_scan_hint_paths(*, logger=None) -> list[str]:
    path = _get_manual_scan_hints_path()
    payload = _load_scan_hint_payload(path, logger=logger)
    if int(payload.get("version") or 0) != _SCAN_HINTS_SCHEMA_VERSION:
        return []

    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list):
        return []

    hint_paths: list[str] = []
    seen_paths: set[str] = set()
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            continue
        candidate = Path(raw_path).expanduser().resolve(strict=False)
        if not is_manual_scan_hint_path_allowed(candidate):
            continue
        normalized = normalize_candidate_path(candidate)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        hint_paths.append(str(candidate))

    return hint_paths


def save_manual_scan_hint(path: str | Path, *, logger=None) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    if not is_manual_scan_hint_path_allowed(candidate):
        return False

    hints_path = _get_manual_scan_hints_path()
    payload = _load_scan_hint_payload(hints_path, logger=logger)
    raw_paths = payload.get("paths")
    stored_paths = list(raw_paths) if isinstance(raw_paths, list) else []

    normalized_candidate = normalize_candidate_path(candidate)
    normalized_paths: dict[str, str] = {}
    for stored_path in stored_paths:
        if not isinstance(stored_path, str):
            continue
        stored_candidate = Path(stored_path).expanduser().resolve(strict=False)
        if not is_manual_scan_hint_path_allowed(stored_candidate):
            continue
        normalized_paths[normalize_candidate_path(stored_candidate)] = str(stored_candidate)

    normalized_paths[normalized_candidate] = str(candidate)

    next_payload = {
        "version": _SCAN_HINTS_SCHEMA_VERSION,
        "paths": list(normalized_paths.values()),
    }

    try:
        hints_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = hints_path.with_suffix(hints_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(next_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, hints_path)
    except Exception as exc:
        if logger:
            logger.debug("Failed to persist manual scan hint to %s: %s", hints_path, exc)
        return False

    return True


__all__ = [
    "is_manual_scan_hint_path_allowed",
    "load_manual_scan_hint_paths",
    "save_manual_scan_hint",
]
