from __future__ import annotations

import os
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only dependency
    winreg = None


def normalize_candidate_path(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False)).lower()


def get_windows_documents_dir() -> Path | None:
    if os.name != "nt" or winreg is None:
        return None

    registry_targets = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"),
    )
    for root_key, sub_key in registry_targets:
        try:
            with winreg.OpenKey(root_key, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
        except OSError:
            continue

        expanded = os.path.expandvars(str(value or "").strip())
        if expanded:
            return Path(expanded)
    return None


def iter_documents_dir_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []

    documents_dir = get_windows_documents_dir()
    if documents_dir is not None:
        candidates.append(documents_dir)

    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        env_value = str(os.environ.get(env_name, "") or "").strip()
        if env_value:
            candidates.append(Path(env_value) / "Documents")

    userprofile = str(os.environ.get("USERPROFILE", "") or "").strip()
    if userprofile:
        candidates.append(Path(userprofile) / "Documents")

    candidates.append(Path.home() / "Documents")

    unique_candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        normalized = normalize_candidate_path(candidate)
        if normalized in seen_candidates:
            continue
        seen_candidates.add(normalized)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


__all__ = [
    "get_windows_documents_dir",
    "iter_documents_dir_candidates",
    "normalize_candidate_path",
]
