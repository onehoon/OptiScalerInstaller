from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from ..common.windows_paths import iter_documents_dir_candidates, normalize_candidate_path


_DOCUMENTS_ENV_TOKEN = "%DOCUMENTS%"
_DOCUMENTS_PREFIXES = {"documents", "document"}
_DOCUMENTS_STYLE_PREFIXES = _DOCUMENTS_PREFIXES | {"my games"}


def _has_path_wildcard(path_part: str) -> bool:
    return any(token in path_part for token in ("*", "?", "["))


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique_paths: list[Path] = []
    seen_paths: set[str] = set()
    for path in paths:
        normalized = normalize_candidate_path(path)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        unique_paths.append(path)
    return tuple(unique_paths)


def _split_relative_path_parts(path_text: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[\\/]+", path_text) if part and part != ".")


def _match_documents_relative_path(base_dir: Path, relative_parts: tuple[str, ...]) -> tuple[Path, ...]:
    if not relative_parts:
        return ()

    current_paths: tuple[Path, ...] = (base_dir,)
    for index, raw_part in enumerate(relative_parts):
        is_last_part = index == len(relative_parts) - 1
        next_paths: list[Path] = []
        pattern = raw_part.lower()
        part_has_wildcard = _has_path_wildcard(raw_part)

        for current_path in current_paths:
            if not current_path.is_dir():
                continue

            if part_has_wildcard:
                try:
                    children = tuple(current_path.iterdir())
                except OSError:
                    continue

                for child in children:
                    if not fnmatch.fnmatch(child.name.lower(), pattern):
                        continue
                    if is_last_part and child.is_file():
                        next_paths.append(child)
                    elif not is_last_part and child.is_dir():
                        next_paths.append(child)
                continue

            child_path = current_path / raw_part
            if is_last_part and child_path.is_file():
                next_paths.append(child_path)
            elif not is_last_part and child_path.is_dir():
                next_paths.append(child_path)

        current_paths = _dedupe_paths(next_paths)
        if not current_paths:
            break

    return current_paths


def _trim_documents_prefix(relative_path: str) -> tuple[str, ...]:
    normalized = str(relative_path or "").strip()
    if not normalized:
        return ()

    if normalized[:len(_DOCUMENTS_ENV_TOKEN)].lower() == _DOCUMENTS_ENV_TOKEN.lower():
        normalized = normalized[len(_DOCUMENTS_ENV_TOKEN):].lstrip("\\/")

    parts = list(_split_relative_path_parts(normalized))
    if parts and parts[0].strip().casefold() in _DOCUMENTS_PREFIXES:
        parts = parts[1:]
    return tuple(parts)


def _resolve_documents_matches(relative_path: str) -> tuple[Path, ...]:
    relative_parts = _trim_documents_prefix(relative_path)
    if not relative_parts:
        return ()

    has_wildcard = any(_has_path_wildcard(part) for part in relative_parts)
    matches: list[Path] = []
    for documents_dir in iter_documents_dir_candidates():
        if has_wildcard:
            matches.extend(_match_documents_relative_path(documents_dir, relative_parts))
            continue

        candidate = documents_dir.joinpath(*relative_parts)
        if candidate.is_file():
            matches.append(candidate)

    return _dedupe_paths(matches)


def _resolve_documents_candidate_path(relative_path: str) -> Path | None:
    matches = _resolve_documents_matches(relative_path)
    if matches:
        return matches[0]

    relative_parts = _trim_documents_prefix(relative_path)
    if not relative_parts:
        return None

    for documents_dir in iter_documents_dir_candidates():
        return documents_dir.joinpath(*relative_parts)
    return None


def resolve_profile_path(
    target_path: str,
    configured_path: str,
    *,
    require_existing: bool,
    logger=None,
) -> Path | None:
    raw_path = str(configured_path or "").strip()
    if not raw_path:
        return None

    expanded_path = Path(os.path.expanduser(os.path.expandvars(raw_path)))
    if expanded_path.is_absolute():
        if require_existing and not expanded_path.is_file():
            return None
        return expanded_path

    first_part = next(iter(_split_relative_path_parts(raw_path)), "").strip().casefold()
    if first_part in _DOCUMENTS_STYLE_PREFIXES:
        documents_candidate = _resolve_documents_candidate_path(raw_path)
        if documents_candidate is not None and (not require_existing or documents_candidate.is_file()):
            return documents_candidate
        if logger:
            logger.info("Profile target not found under Documents: %s", raw_path)
        return None

    direct_candidate = Path(target_path) / raw_path
    if not require_existing or direct_candidate.is_file():
        return direct_candidate

    documents_candidate = _resolve_documents_candidate_path(raw_path)
    if documents_candidate is not None and documents_candidate.is_file():
        return documents_candidate

    documents_fallback = _resolve_documents_candidate_path(f"Documents\\{raw_path}")
    if documents_fallback is not None and documents_fallback.is_file():
        return documents_fallback

    return None


__all__ = [
    "resolve_profile_path",
]
