from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


_SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _normalize_apps_script_base_url(base_url_or_key: str) -> str:
    raw = str(base_url_or_key or "").strip()
    if not raw:
        raise ValueError("GPU bundle URL is empty")

    low = raw.lower()
    if low.startswith("https://") or low.startswith("http://"):
        return raw

    if _SCRIPT_ID_RE.fullmatch(raw):
        return f"https://script.google.com/macros/s/{raw}/exec"

    raise ValueError(f"Invalid GPU bundle URL or Apps Script key: {raw}")


def build_gpu_bundle_request_url(base_url_or_key: str, *, gpu_vendor: str, gpu_model: str) -> str:
    base_url = _normalize_apps_script_base_url(base_url_or_key)
    parsed = urlparse(base_url)
    preserved = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in {"action", "vendor", "gpu"}
    ]

    request_query = preserved + [
        ("action", "getSupportedGameBundle"),
        ("vendor", str(gpu_vendor or "").strip().lower()),
        ("gpu", str(gpu_model or "").strip()),
    ]
    return urlunparse(parsed._replace(query=urlencode(request_query, doseq=True)))


def load_supported_game_bundle(
    base_url_or_key: str,
    gpu_vendor: str,
    gpu_model: str,
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, dict[str, Any]]:
    request_url = build_gpu_bundle_request_url(
        base_url_or_key,
        gpu_vendor=gpu_vendor,
        gpu_model=gpu_model,
    )
    response = requests.get(request_url, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, Mapping):
        raise ValueError("GPU bundle response must be a JSON object")

    if payload.get("ok") is False:
        raise ValueError(str(payload.get("error") or "GPU bundle request failed"))

    shared_profiles = payload.get("profiles") if isinstance(payload.get("profiles"), Mapping) else {}

    games_obj = payload.get("games")
    if games_obj is None and all(isinstance(v, Mapping) for v in payload.values()):
        # Backward-compatible format: {"ffxvi": {...}, ...}
        return _normalize_bundle_games(payload, shared_profiles=shared_profiles)

    return _normalize_bundle_games(games_obj, shared_profiles=shared_profiles)


def _normalize_bundle_games(games_obj: Any, *, shared_profiles: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    shared_profiles = shared_profiles or {}
    bundle: dict[str, dict[str, Any]] = {}

    if isinstance(games_obj, Mapping):
        items = list(games_obj.values())
    elif isinstance(games_obj, list):
        items = games_obj
    else:
        items = []

    for raw in items:
        if not isinstance(raw, Mapping):
            continue

        game_id = str(raw.get("game_id") or "").strip()
        if not game_id:
            continue

        entry = dict(raw)
        if shared_profiles and "shared_profiles" not in entry:
            entry["shared_profiles"] = dict(shared_profiles)
        bundle[game_id.casefold()] = entry

    return bundle


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_int(value: object, default: int = 100) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _normalize_profile_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _resolve_layered_optiscaler_ini_rows(bundle_entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    local_rows = [row for row in list(bundle_entry.get("optiscaler_ini") or []) if isinstance(row, Mapping)]

    shared_profiles = bundle_entry.get("shared_profiles")
    if not isinstance(shared_profiles, Mapping):
        return [dict(row) for row in local_rows]

    shared_ini_rows = [row for row in list(shared_profiles.get("optiscaler_ini") or []) if isinstance(row, Mapping)]
    if not shared_ini_rows:
        return [dict(row) for row in local_rows]

    game_id = str(bundle_entry.get("game_id") or "").strip()
    profile_id = str(bundle_entry.get("profile_id") or "").strip()
    vendor = str(bundle_entry.get("bundle_gpu_vendor") or "").strip().lower()

    active_profile_ids = {"global_all"}
    if vendor and vendor not in {"all", "default"}:
        active_profile_ids.add(f"global_{vendor}")
    if game_id:
        active_profile_ids.add(f"{game_id.casefold()}_all")
    if profile_id:
        active_profile_ids.add(profile_id.casefold())

    layered_rows = []
    for row in shared_ini_rows:
        profile_key = _normalize_profile_key(row.get("profile_id"))
        if profile_key and profile_key in active_profile_ids:
            layered_rows.append(dict(row))

    layered_rows.extend(dict(row) for row in local_rows)
    return layered_rows


def _materialize_ini_settings(rows: list[dict[str, Any]]) -> dict[str, str]:
    selected: dict[tuple[str, str], tuple[int, str]] = {}
    for row in rows:
        section = str(row.get("section") or "").strip()
        key = str(row.get("key") or "").strip()
        if not section or not key:
            continue

        composite_key = (section, key)
        priority = _safe_int(row.get("priority"), 100)
        value = str(row.get("value") or "")

        current = selected.get(composite_key)
        if current is None or priority < current[0]:
            selected[composite_key] = (priority, value)

    return {f"{section}:{key}": value for (section, key), (_priority, value) in selected.items()}


def _apply_install_profile(game_entry: dict[str, Any], install_profile: Mapping[str, Any]) -> None:
    if "optiscaler_dll_name" in install_profile:
        game_entry["dll_name"] = str(install_profile.get("optiscaler_dll_name") or "").strip()

    if "ultimate_asi_loader" in install_profile:
        game_entry["ultimate_asi_loader"] = _to_bool(install_profile.get("ultimate_asi_loader"), False)
    if "optipatcher" in install_profile:
        game_entry["optipatcher"] = _to_bool(install_profile.get("optipatcher"), False)
    if "specialk" in install_profile:
        game_entry["specialk"] = _to_bool(install_profile.get("specialk"), False)
    if "reframework_url" in install_profile:
        game_entry["reframework_url"] = str(install_profile.get("reframework_url") or "").strip()
    if "unreal5" in install_profile:
        game_entry["unreal5"] = _to_bool(install_profile.get("unreal5"), False)
        game_entry.setdefault("unreal5_rule", "")
    if "rtss_overlay" in install_profile:
        game_entry["rtss_overlay"] = _to_bool(install_profile.get("rtss_overlay"), False)

    game_entry["module_dl"] = str(game_entry.get("module_dl") or "").strip()
    game_entry.setdefault("unreal5_rule", "")


def merge_gpu_bundle_into_game_db(
    game_db: dict[str, dict[str, Any]],
    bundle: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {key: dict(value) for key, value in dict(game_db or {}).items()}
    normalized_bundle = {
        str((value or {}).get("game_id") or "").casefold(): dict(value)
        for value in dict(bundle or {}).values()
        if isinstance(value, Mapping) and str((value or {}).get("game_id") or "").strip()
    }

    game_id_index: dict[str, list[str]] = {}
    for game_key, game_entry in merged.items():
        game_entry["__gpu_bundle_loaded__"] = True
        game_entry["__gpu_bundle_supported__"] = False

        game_id = str(game_entry.get("game_id") or "").strip().casefold()
        if game_id:
            game_id_index.setdefault(game_id, []).append(game_key)

    for game_id, bundle_entry in normalized_bundle.items():
        target_keys = game_id_index.get(game_id, [])
        if not target_keys:
            continue

        install_profile = bundle_entry.get("install_profile") if isinstance(bundle_entry.get("install_profile"), Mapping) else {}
        is_enabled = _to_bool(install_profile.get("enabled"), True)

        for target_key in target_keys:
            game_entry = merged[target_key]
            game_entry["__gpu_bundle_loaded__"] = True
            game_entry["__gpu_bundle_supported__"] = bool(is_enabled)
            game_entry["__gpu_profile_id__"] = str(bundle_entry.get("profile_id") or "").strip()
            if bundle_entry.get("bundle_gpu_vendor"):
                game_entry["__gpu_bundle_vendor__"] = str(bundle_entry.get("bundle_gpu_vendor") or "").strip().lower()

            _apply_install_profile(game_entry, install_profile)

            layered_optiscaler_ini = _resolve_layered_optiscaler_ini_rows(bundle_entry)
            game_entry["ini_settings"] = _materialize_ini_settings(layered_optiscaler_ini)

            game_entry["game_ini_profile"] = [
                dict(row)
                for row in list(bundle_entry.get("game_ini") or [])
                if isinstance(row, Mapping)
            ]
            game_entry["engine_ini_profile"] = [
                dict(row)
                for row in list(bundle_entry.get("engine_ini") or [])
                if isinstance(row, Mapping)
            ]
            game_entry["game_xml_profile"] = [
                dict(row)
                for row in list(bundle_entry.get("game_xml") or [])
                if isinstance(row, Mapping)
            ]
            game_entry["registry_profile"] = [
                dict(row)
                for row in list(bundle_entry.get("registry") or [])
                if isinstance(row, Mapping)
            ]

    return merged


__all__ = [
    "build_gpu_bundle_request_url",
    "load_supported_game_bundle",
    "merge_gpu_bundle_into_game_db",
]