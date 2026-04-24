from __future__ import annotations

from pathlib import Path


def resolve_cached_archive_path(cached_archive_path: str) -> Path | None:
    normalized = str(cached_archive_path or "").strip()
    if not normalized:
        return None
    cached = Path(normalized)
    return cached if cached.is_file() else None


__all__ = [
    "resolve_cached_archive_path",
]
