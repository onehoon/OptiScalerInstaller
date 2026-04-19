from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any


def _decode_json_pointer_token(token: str) -> str:
    return str(token or "").replace("~1", "/").replace("~0", "~")


def parse_json_pointer(json_path: str) -> tuple[str, ...]:
    normalized = str(json_path or "").strip()
    if not normalized or not normalized.startswith("/"):
        raise ValueError("json_path must be a JSON Pointer starting with '/'")
    return tuple(_decode_json_pointer_token(token) for token in normalized.split("/")[1:])


def coerce_json_value(value: object, value_type: str = "") -> Any:
    normalized_type = str(value_type or "").strip().casefold()
    if normalized_type in {"string", "str", ""}:
        return "" if value is None else str(value)
    if normalized_type in {"int", "integer"}:
        return int(value)
    if normalized_type in {"float", "double"}:
        return float(value)
    if normalized_type in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        normalized_value = str(value or "").strip().casefold()
        if normalized_value in {"1", "true", "yes", "on"}:
            return True
        if normalized_value in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Unsupported bool value: {value!r}")
    if normalized_type == "null":
        return None
    if normalized_type == "json":
        if isinstance(value, str):
            return json.loads(value)
        return value
    return value


def _resolve_existing_json_target(document: Any, tokens: Sequence[str]) -> tuple[Any, str] | None:
    if not tokens:
        return None

    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                index = int(token)
            except (TypeError, ValueError):
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        return None

    return current, tokens[-1]


def _set_existing_json_value(container: Any, token: str, value: Any) -> bool:
    if isinstance(container, dict):
        if token not in container:
            return False
        container[token] = value
        return True

    if isinstance(container, list):
        try:
            index = int(token)
        except (TypeError, ValueError):
            return False
        if index < 0 or index >= len(container):
            return False
        container[index] = value
        return True

    return False


def apply_json_settings(file_path: str | Path, rows: Sequence[Mapping[str, Any]], logger=None) -> bool:
    path = Path(file_path)
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = False

    for row in rows:
        json_path = str(row.get("json_path") or "").strip()
        op = str(row.get("op") or "set").strip().casefold() or "set"
        if not json_path:
            continue
        if op != "set":
            if logger:
                logger.info("Skipped game_json_profile row with unsupported op '%s': %s", op, json_path)
            continue

        try:
            tokens = parse_json_pointer(json_path)
            coerced_value = coerce_json_value(row.get("value"), str(row.get("value_type") or ""))
        except Exception:
            if logger:
                logger.exception("Failed to parse game_json_profile row for %s", json_path)
            continue

        resolved = _resolve_existing_json_target(document, tokens)
        if resolved is None:
            if logger:
                logger.info("Skipped game_json_profile because json_path was not found: %s", json_path)
            continue

        container, token = resolved
        if not _set_existing_json_value(container, token, coerced_value):
            if logger:
                logger.info("Skipped game_json_profile because target value was not found: %s", json_path)
            continue
        changed = True

    if changed:
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return changed


__all__ = [
    "apply_json_settings",
    "coerce_json_value",
    "parse_json_pointer",
]
