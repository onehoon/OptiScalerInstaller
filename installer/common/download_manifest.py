"""Persistent download manifest for tracking cached archive versions.

The manifest is stored as a JSON file under the app's cache directory.
Each entry maps a module key to its cached version string and the
timestamp when it was last downloaded.

For optiscaler and fsr4int8, the ``version`` field holds the archive
filename (which encodes the version).  For optipatcher, specialk,
ultimateasiloader, and unreal5 the field holds the ``version`` column
value from the Google Sheet.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


_MANIFEST_FILENAME = "cache_manifest.json"
_logger = logging.getLogger(__name__)


def _manifest_path(cache_root: Path) -> Path:
    return cache_root / _MANIFEST_FILENAME


def read_manifest(cache_root: Path) -> dict[str, dict]:
    """Return the manifest dict, or an empty dict on any read/parse error."""
    path = _manifest_path(cache_root)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:
        _logger.debug("Failed to read download manifest at %s: %s", path, exc)
        return {}


def write_manifest_entry(cache_root: Path, key: str, version: str) -> None:
    """Upsert one entry in the manifest.  Silently ignores write errors."""
    path = _manifest_path(cache_root)
    try:
        manifest = read_manifest(cache_root)
        manifest[key] = {
            "version": str(version or ""),
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        _logger.debug("Failed to write manifest entry %r at %s: %s", key, path, exc)


def get_cached_version(cache_root: Path, key: str) -> str:
    """Return the cached version string for *key*, or ``""`` if not found."""
    return str(read_manifest(cache_root).get(key, {}).get("version", "") or "")


def is_update_needed(cache_root: Path, key: str, sheet_version: str) -> bool:
    """Return True when the sheet version differs from the cached version.

    If *sheet_version* is empty (null from the sheet), comparison falls back
    to URL-based logic — treated as "unknown version" and always returns False
    (no forced re-download when version info is absent) so existing caches are
    reused until the sheet is populated.
    """
    if not sheet_version:
        return False
    cached = get_cached_version(cache_root, key)
    return cached != str(sheet_version).strip()


__all__ = [
    "get_cached_version",
    "is_update_needed",
    "read_manifest",
    "write_manifest_entry",
]
