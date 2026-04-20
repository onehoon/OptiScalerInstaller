from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from pathlib import Path
import re
import shutil
import uuid
import zipfile

from ..common.download_manifest import write_manifest_entry
from ..install import services as installer_services
from ..install.payload_utils import resolve_payload_source_dir, validate_optiscaler_payload_dir


DownloadToFile = Callable[..., None]
_OPTISCALER_STAGE_PREFIX = ".optiscaler_stage_"
_OPTISCALER_BACKUP_PREFIX = ".optiscaler_backup_"
_OPTISCALER_MANIFEST_FILENAME = "cache_manifest.json"
_OPTISCALER_CACHE_FILE_SUFFIXES = {".7z", ".zip", ".rar", ".tar", ".gz", ".xz", ".bz2", ".asi"}


def _normalize_entry(entry: Mapping[str, object] | None) -> dict[str, object]:
    return dict(entry) if isinstance(entry, Mapping) else {}


def _resolve_archive_filename(entry: Mapping[str, object] | None) -> str:
    normalized_entry = _normalize_entry(entry)
    filename = str(normalized_entry.get("filename", "") or normalized_entry.get("version", "")).strip()
    if filename:
        return Path(filename).name

    url = str(normalized_entry.get("url", "")).strip()
    return Path(url).name if url else ""


def resolve_optiscaler_cache_version(entry: Mapping[str, object] | None) -> str:
    normalized_entry = _normalize_entry(entry)
    version = str(normalized_entry.get("version", "")).strip()
    if version:
        return version
    return _resolve_archive_filename(normalized_entry)


def resolve_optiscaler_cache_entry_name(entry: Mapping[str, object] | None) -> str:
    filename = _resolve_archive_filename(entry)
    candidate = Path(filename).stem if filename else resolve_optiscaler_cache_version(entry)
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(candidate or "").strip()).strip("._-")
    return normalized or "optiscaler"


def resolve_optiscaler_payload_cache_dir(cache_dir: Path | str, entry: Mapping[str, object] | None) -> Path:
    cache_root = Path(cache_dir)
    return cache_root / resolve_optiscaler_cache_entry_name(entry) / "payload"


def is_valid_optiscaler_payload_cache(
    payload_dir: Path | str,
    *,
    logger=None,
) -> bool:
    active_logger = logger or logging.getLogger()
    payload_path = Path(payload_dir)
    try:
        validate_optiscaler_payload_dir(payload_path)
        return True
    except Exception as exc:
        if payload_path.exists():
            active_logger.info("[APP] Cached OptiScaler payload is invalid, rebuilding: %s (%s)", payload_path, exc)
        return False


def prepare_optiscaler_payload_cache(
    entry: Mapping[str, object] | None,
    cache_dir: Path | str,
    *,
    download_to_file: DownloadToFile,
    manifest_root: Path | None = None,
    logger=None,
) -> Path:
    active_logger = logger or logging.getLogger()
    normalized_entry = _normalize_entry(entry)
    url = str(normalized_entry.get("url", "")).strip()
    filename = _resolve_archive_filename(normalized_entry)
    if not url or not filename:
        raise ValueError("OptiScaler payload cache preparation requires both url and filename metadata")

    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_entry_name = resolve_optiscaler_cache_entry_name(normalized_entry)
    cache_version = resolve_optiscaler_cache_version(normalized_entry)
    final_entry_dir = cache_root / cache_entry_name
    final_payload_dir = final_entry_dir / "payload"

    work_root = cache_root / f"{_OPTISCALER_STAGE_PREFIX}{cache_entry_name}_{uuid.uuid4().hex}"
    staging_entry_dir = work_root / cache_entry_name
    staging_payload_dir = staging_entry_dir / "payload"
    extract_root = work_root / "_extract"
    download_path = work_root / filename
    backup_entry_dir = cache_root / f"{_OPTISCALER_BACKUP_PREFIX}{cache_entry_name}_{uuid.uuid4().hex}"

    active_logger.info("[APP] Preparing OptiScaler payload cache: %s", cache_entry_name)
    try:
        work_root.mkdir(parents=True, exist_ok=False)
        staging_entry_dir.mkdir(parents=True, exist_ok=False)

        download_to_file(url, str(download_path), timeout=300)
        if download_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(download_path):
            raise RuntimeError(f"Downloaded OptiScaler archive is not a valid zip file: {download_path}")

        installer_services.extract_archive(str(download_path), str(extract_root), logger=active_logger)
        normalized_source = resolve_payload_source_dir(extract_root)
        if not normalized_source.is_dir():
            raise FileNotFoundError(f"Resolved OptiScaler payload root was not a directory: {normalized_source}")

        shutil.copytree(normalized_source, staging_payload_dir)
        validate_optiscaler_payload_dir(staging_payload_dir)

        if final_entry_dir.exists():
            active_logger.info("[APP] Replacing existing OptiScaler payload cache: %s", final_entry_dir)
            final_entry_dir.rename(backup_entry_dir)

        staging_entry_dir.rename(final_entry_dir)
        active_logger.info("[APP] OptiScaler payload cache ready: %s", final_payload_dir)

        if backup_entry_dir.exists():
            shutil.rmtree(backup_entry_dir, ignore_errors=True)

        _cleanup_stale_optiscaler_cache_entries(cache_root, keep_entry_name=cache_entry_name, logger=active_logger)

        if manifest_root is not None and cache_version:
            write_manifest_entry(
                manifest_root,
                "optiscaler",
                cache_version,
                filename=filename,
                cache_kind="payload_dir",
                cache_entry=cache_entry_name,
            )

        return final_payload_dir
    except Exception:
        if backup_entry_dir.exists() and not final_entry_dir.exists():
            try:
                backup_entry_dir.rename(final_entry_dir)
                active_logger.info("[APP] Restored previous OptiScaler payload cache after failure: %s", final_entry_dir)
            except Exception:
                active_logger.exception(
                    "[APP] Failed to restore previous OptiScaler payload cache after failure: %s",
                    backup_entry_dir,
                )
        raise
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
        if backup_entry_dir.exists() and final_entry_dir.exists():
            shutil.rmtree(backup_entry_dir, ignore_errors=True)


def _cleanup_stale_optiscaler_cache_entries(cache_dir: Path, *, keep_entry_name: str, logger=None) -> None:
    active_logger = logger or logging.getLogger()
    if not cache_dir.exists():
        return

    keep_name = str(keep_entry_name or "").strip().casefold()
    for child in sorted(cache_dir.iterdir(), key=lambda path: path.name.casefold()):
        if keep_name and child.name.casefold() == keep_name:
            continue
        if child.name == _OPTISCALER_MANIFEST_FILENAME:
            continue

        should_remove = child.is_dir() or child.suffix.lower() in _OPTISCALER_CACHE_FILE_SUFFIXES
        if not should_remove:
            continue

        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=False)
            else:
                child.unlink()
            if not (
                child.name.startswith(_OPTISCALER_STAGE_PREFIX)
                or child.name.startswith(_OPTISCALER_BACKUP_PREFIX)
            ):
                active_logger.info("[APP] Removed stale OptiScaler cache entry: %s", child)
        except OSError:
            active_logger.warning("[APP] Failed to remove stale OptiScaler cache entry: %s", child, exc_info=True)


__all__ = [
    "is_valid_optiscaler_payload_cache",
    "prepare_optiscaler_payload_cache",
    "resolve_optiscaler_cache_entry_name",
    "resolve_optiscaler_cache_version",
    "resolve_optiscaler_payload_cache_dir",
]
