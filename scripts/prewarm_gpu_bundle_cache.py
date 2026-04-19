from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during ad-hoc use
    load_dotenv = None


_SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_DELAY_SECONDS = 0.2
_SUPPORTED_VENDORS = ("amd", "nvidia", "intel")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_repo_env() -> None:
    env_path = _repo_root() / ".env"
    if not env_path.exists():
        return

    if load_dotenv is not None:
        load_dotenv(env_path, override=False)
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _normalize_base_url(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("Apps Script URL is empty. Pass --base-url or set OPTISCALER_GPU_BUNDLE_URL.")

    lowered = value.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return value

    if _SCRIPT_ID_RE.fullmatch(value):
        return f"https://script.google.com/macros/s/{value}/exec"

    raise ValueError(f"Invalid Apps Script URL or script id: {value}")


def _load_models_from_json_text(raw_text: str, *, source_label: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GPU model list JSON is invalid in {source_label}: {exc}") from exc

    return _normalize_models_payload(payload, source_label=source_label)


def _resolve_models_file(explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_absolute():
            path = (_repo_root() / path).resolve()
        return path

    return Path(__file__).with_name("gpu_cache_models.json")


def _normalize_models_payload(payload: Any, *, source_label: str) -> dict[str, list[str]]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"GPU model list must be a JSON object keyed by vendor in {source_label}.")

    models_by_vendor: dict[str, list[str]] = {}
    for vendor, raw_models in payload.items():
        normalized_vendor = str(vendor or "").strip().lower()
        if not normalized_vendor:
            continue
        if normalized_vendor not in _SUPPORTED_VENDORS:
            raise ValueError(
                f"Unsupported vendor '{vendor}' in {source_label}. Use one of: {', '.join(_SUPPORTED_VENDORS)}."
            )
        if not isinstance(raw_models, list):
            raise ValueError(f"Vendor '{vendor}' must map to a JSON list in {source_label}.")

        seen: set[str] = set()
        normalized_models: list[str] = []
        for item in raw_models:
            model = " ".join(str(item or "").split()).strip()
            if not model:
                continue
            dedupe_key = model.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized_models.append(model)
        models_by_vendor[normalized_vendor] = normalized_models

    return models_by_vendor


def _load_models(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"GPU model list file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_models_payload(payload, source_label=str(path))


def _iter_requests(models_by_vendor: Mapping[str, Iterable[str]]) -> list[tuple[str, str]]:
    requests_to_make: list[tuple[str, str]] = []
    for vendor in _SUPPORTED_VENDORS:
        for model in models_by_vendor.get(vendor, ()):
            requests_to_make.append((vendor, str(model)))
    return requests_to_make


def _build_request_url(base_url: str, *, vendor: str, gpu_model: str, debug: bool, force: bool = False) -> str:
    params = {
        "action": "getSupportedGameBundle",
        "vendor": vendor,
        "gpu": gpu_model,
    }
    if debug:
        params["debug"] = "1"
    if force:
        params["force"] = "1"
    return f"{base_url}?{urlencode(params)}"


def _extract_error_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        error_value = payload.get("error")
        if error_value:
            return str(error_value)
    return "Unknown Apps Script error"


def _request_bundle(
    session: requests.Session,
    *,
    base_url: str,
    vendor: str,
    gpu_model: str,
    timeout_seconds: float,
    debug: bool,
) -> tuple[bool, float, str]:
    request_url = _build_request_url(base_url, vendor=vendor, gpu_model=gpu_model, debug=debug, force=getattr(_request_bundle, "force", False))
    started = time.perf_counter()
    response = session.get(request_url, timeout=timeout_seconds)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("Apps Script response must be a JSON object.")
    if payload.get("ok") is False:
        raise RuntimeError(_extract_error_text(payload))

    debug_payload = payload.get("debug") if isinstance(payload.get("debug"), Mapping) else {}
    cache_hit = bool(debug_payload.get("cache_hit")) if debug_payload else False
    cache_key = str(debug_payload.get("cache_key") or "")
    if debug_payload and cache_key:
        note = f"cache_hit={cache_hit} key={cache_key}"
    elif debug_payload:
        note = f"cache_hit={cache_hit}"
    else:
        note = ""
    return cache_hit, elapsed_ms, note


def main() -> int:

    parser = argparse.ArgumentParser(
        description="Prewarm OptiScaler Apps Script GPU bundle cache using an editable model list."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force cache overwrite (force=1 param to Apps Script)",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Apps Script web app URL or script id. Defaults to OPTISCALER_GPU_BUNDLE_URL from .env/environment.",
    )
    parser.add_argument(
        "--models-file",
        default="",
        help="JSON file containing vendor -> GPU model list. Defaults to scripts/gpu_cache_models.json.",
    )
    parser.add_argument(
        "--models-json",
        default="",
        help="Inline JSON text for vendor -> GPU model list. Useful for CI secrets.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request timeout in seconds. Default: {_DEFAULT_TIMEOUT_SECONDS:g}",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=_DEFAULT_DELAY_SECONDS,
        help=f"Delay between requests in seconds. Default: {_DEFAULT_DELAY_SECONDS:g}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Pass debug=1 to the endpoint and print cache metadata when the Apps Script supports it.",
    )
    args = parser.parse_args()

    _load_repo_env()

    models_source_description = ""
    try:
        # Patch _request_bundle to pass force argument
        setattr(_request_bundle, "force", args.force)
        base_url = _normalize_base_url(args.base_url or os.environ.get("OPTISCALER_GPU_BUNDLE_URL", ""))
        inline_models_json = str(args.models_json or os.environ.get("OPTISCALER_GPU_CACHE_MODELS_JSON", "")).strip()
        if inline_models_json:
            models_by_vendor = _load_models_from_json_text(
                inline_models_json,
                source_label="--models-json/OPTISCALER_GPU_CACHE_MODELS_JSON",
            )
            models_source_description = "inline JSON"
        else:
            models_file_arg = args.models_file or os.environ.get("OPTISCALER_GPU_CACHE_MODELS_FILE", "")
            models_path = _resolve_models_file(models_file_arg or None)
            models_by_vendor = _load_models(models_path)
            models_source_description = str(models_path)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    request_items = _iter_requests(models_by_vendor)
    if not request_items:
        print(f"[ERROR] No GPU models found in {models_source_description}", file=sys.stderr)
        return 1

    print(f"Apps Script: {base_url}")
    print(f"Models source: {models_source_description}")
    print(f"Requests: {len(request_items)}")

    session = requests.Session()
    failures = 0

    for index, (vendor, gpu_model) in enumerate(request_items, start=1):
        try:
            cache_hit, elapsed_ms, note = _request_bundle(
                session,
                base_url=base_url,
                vendor=vendor,
                gpu_model=gpu_model,
                timeout_seconds=max(float(args.timeout or 0.0), 1.0),
                debug=bool(args.debug),
            )
            status_text = "HIT" if cache_hit else "OK"
            suffix = f" {note}" if note else ""
            print(f"[{index:02d}/{len(request_items):02d}] {status_text:<3} {vendor:<6} {gpu_model} ({elapsed_ms:.0f} ms){suffix}")
        except Exception as exc:
            failures += 1
            print(f"[{index:02d}/{len(request_items):02d}] FAIL {vendor:<6} {gpu_model} :: {exc}", file=sys.stderr)

        if index < len(request_items) and args.delay > 0:
            time.sleep(max(float(args.delay), 0.0))

    print(f"Finished with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
