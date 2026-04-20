from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from typing import Any

from ..common.network_utils import get_shared_retry_session


_PROFILE_SESSION = get_shared_retry_session()


@dataclass(frozen=True)
class ProfileCatalogs:
    game_ini_profile: dict[str, tuple[dict[str, Any], ...]]
    game_unreal_ini_profile: dict[str, tuple[dict[str, Any], ...]]
    engine_ini_profile: dict[str, tuple[dict[str, Any], ...]]
    game_xml_profile: dict[str, tuple[dict[str, Any], ...]]
    registry_profile: dict[str, tuple[dict[str, Any], ...]]
    game_json_profile: dict[str, tuple[dict[str, Any], ...]]


def _normalize_profile_id(value: object) -> str:
    return str(value or "").strip().casefold()


def _derive_all_profile_id(profile_id: str) -> str:
    """Derive the _ALL wildcard profile id from a specific profile id.

    Rule: game IDs never contain '_', so the first '_' separates game_id from
    the rest.  'kcd2_intel' → 'kcd2_all', 'kcd2_intel_igpu' → 'kcd2_all'.
    Returns '' when there is no '_' (bare game_id) or when profile_id already
    IS the _all id (to avoid duplicate rows).
    """
    if not profile_id or "_" not in profile_id:
        return ""
    game_id_part = profile_id.split("_", 1)[0]
    all_id = game_id_part + "_all"
    # Avoid self-reference if profile_id is already the _all id
    if all_id == profile_id:
        return ""
    return all_id


def _get_profile_rows(
    catalog: dict[str, tuple[dict[str, Any], ...]],
    profile_id: str,
) -> list[dict[str, Any]]:
    """Return _ALL rows first, then vendor-specific rows (specific overrides _ALL)."""
    all_id = _derive_all_profile_id(profile_id)
    all_rows = list(catalog.get(all_id, ())) if all_id else []
    specific_rows = list(catalog.get(profile_id, ()))
    return [dict(r) for r in all_rows + specific_rows]


def _load_profile_rows(source_url: str, *, label: str, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    normalized = str(source_url or "").strip()
    if not normalized:
        raise ValueError(f"{label} URL is empty")

    response = _PROFILE_SESSION.get(normalized, timeout=timeout_seconds)
    response.raise_for_status()
    rows = json.loads(response.content.decode("utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError(f"{label} must contain a list")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _build_profile_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[dict[str, Any], ...]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        profile_id = _normalize_profile_id(row.get("profile_id"))
        if not profile_id:
            continue
        indexed.setdefault(profile_id, []).append(dict(row))
    return {
        profile_id: tuple(dict(item) for item in profile_rows)
        for profile_id, profile_rows in indexed.items()
    }


def load_profile_catalogs(
    game_ini_profile_url: str,
    engine_ini_profile_url: str,
    game_xml_profile_url: str,
    registry_profile_url: str,
    game_json_profile_url: str = "",
    game_unreal_ini_profile_url: str = "",
    *,
    timeout_seconds: float = 10.0,
) -> ProfileCatalogs:
    fetch_specs = [
        ("game_ini_profile", game_ini_profile_url, "game_ini_profile.json"),
        ("engine_ini_profile", engine_ini_profile_url, "engine_ini_profile.json"),
        ("game_xml_profile", game_xml_profile_url, "game_xml_profile.json"),
        ("registry_profile", registry_profile_url, "registry_profile.json"),
    ]
    if str(game_unreal_ini_profile_url or "").strip():
        fetch_specs.append(("game_unreal_ini_profile", game_unreal_ini_profile_url, "game_unreal_ini_profile.json"))
    if str(game_json_profile_url or "").strip():
        fetch_specs.append(("game_json_profile", game_json_profile_url, "game_json_profile.json"))

    loaded_rows: dict[str, list[dict[str, Any]]] = {
        "game_ini_profile": [],
        "game_unreal_ini_profile": [],
        "engine_ini_profile": [],
        "game_xml_profile": [],
        "registry_profile": [],
        "game_json_profile": [],
    }
    with ThreadPoolExecutor(max_workers=len(fetch_specs)) as executor:
        future_by_name = {
            name: executor.submit(
                _load_profile_rows,
                url,
                label=label,
                timeout_seconds=timeout_seconds,
            )
            for name, url, label in fetch_specs
        }
        for name, future in future_by_name.items():
            loaded_rows[name] = future.result()

    return ProfileCatalogs(
        game_ini_profile=_build_profile_index(loaded_rows["game_ini_profile"]),
        game_unreal_ini_profile=_build_profile_index(loaded_rows["game_unreal_ini_profile"]),
        engine_ini_profile=_build_profile_index(loaded_rows["engine_ini_profile"]),
        game_xml_profile=_build_profile_index(loaded_rows["game_xml_profile"]),
        registry_profile=_build_profile_index(loaded_rows["registry_profile"]),
        game_json_profile=_build_profile_index(loaded_rows["game_json_profile"]),
    )


def attach_profile_catalogs_to_game_db(
    game_db: Mapping[str, Mapping[str, Any]],
    catalogs: ProfileCatalogs,
) -> dict[str, dict[str, Any]]:
    attached: dict[str, dict[str, Any]] = {}
    for game_key, raw_game_entry in dict(game_db or {}).items():
        game_entry = dict(raw_game_entry)
        profile_id = _normalize_profile_id(game_entry.get("__gpu_profile_id__"))
        game_entry["game_ini_profile"] = _get_profile_rows(catalogs.game_ini_profile, profile_id)
        game_entry["game_unreal_ini_profile"] = _get_profile_rows(catalogs.game_unreal_ini_profile, profile_id)
        game_entry["engine_ini_profile"] = _get_profile_rows(catalogs.engine_ini_profile, profile_id)
        game_entry["game_xml_profile"] = _get_profile_rows(catalogs.game_xml_profile, profile_id)
        game_entry["registry_profile"] = _get_profile_rows(catalogs.registry_profile, profile_id)
        game_entry["game_json_profile"] = _get_profile_rows(catalogs.game_json_profile, profile_id)
        attached[str(game_key)] = game_entry
    return attached


__all__ = [
    "ProfileCatalogs",
    "attach_profile_catalogs_to_game_db",
    "load_profile_catalogs",
]
