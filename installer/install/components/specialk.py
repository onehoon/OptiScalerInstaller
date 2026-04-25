from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .. import services as installer_services
from ..archive_source import resolve_cached_archive_path
from ._link_utils import extract_module_url


SPECIALK64_DLL_NAME = "SpecialK64.dll"
SPECIALK_ARCHIVE_EXTENSIONS = {".zip", ".7z"}


def _resolve_specialk_download_name(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    file_name = os.path.basename(parsed.path).strip()
    suffix = Path(file_name).suffix.lower()
    if file_name and (suffix in SPECIALK_ARCHIVE_EXTENSIONS or file_name.lower() == SPECIALK64_DLL_NAME.lower()):
        return file_name
    return "SpecialK.7z"


def _resolve_specialk_payload(download_path: Path, extract_dir: Path, logger=None) -> Path:
    if download_path.suffix.lower() not in SPECIALK_ARCHIVE_EXTENSIONS:
        if download_path.name.lower() != SPECIALK64_DLL_NAME.lower():
            raise FileNotFoundError(f"Special K payload must be {SPECIALK64_DLL_NAME}: {download_path.name}")
        return download_path

    installer_services.extract_archive(str(download_path), str(extract_dir), logger=logger)
    candidates = [
        candidate
        for candidate in extract_dir.rglob("*")
        if candidate.is_file() and candidate.name.lower() == SPECIALK64_DLL_NAME.lower()
    ]
    if not candidates:
        raise FileNotFoundError(f"{SPECIALK64_DLL_NAME} was not found inside the Special K archive")
    if len(candidates) > 1:
        normalized_candidates = ", ".join(
            sorted(str(candidate.relative_to(extract_dir)).replace("\\", "/") for candidate in candidates)
        )
        raise RuntimeError(f"Multiple {SPECIALK64_DLL_NAME} payload candidates were found: {normalized_candidates}")
    return candidates[0]


def install_specialk_payload(target_path, final_dll_name, url="", logger=None, cached_archive_path=""):
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    normalized_final_name = Path(str(final_dll_name or "").strip()).name
    if not normalized_final_name:
        raise RuntimeError("Special K install requires the final OptiScaler DLL name.")

    plugins_dir = target_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    destination_path = plugins_dir / normalized_final_name

    cached = resolve_cached_archive_path(cached_archive_path)
    use_cache = cached is not None
    normalized_url = str(url or "").strip()

    if not use_cache and not normalized_url:
        raise FileNotFoundError("Special K download link is not configured")

    tmpdir_path = target_dir / f".optiscaler_specialk_tmp_{uuid.uuid4().hex}"
    tmpdir_path.mkdir(parents=False, exist_ok=False)
    try:
        if use_cache:
            download_path = cached
        else:
            download_name = _resolve_specialk_download_name(normalized_url)
            download_path = tmpdir_path / download_name
            installer_services.download_to_file(normalized_url, str(download_path), timeout=60, logger=logger)

        extract_dir = tmpdir_path / "payload"
        payload_path = _resolve_specialk_payload(download_path, extract_dir, logger=logger)

        if destination_path.exists():
            if not destination_path.is_file():
                raise RuntimeError(f"Existing Special K plugin destination is not a file: {destination_path}")
            installer_services.ensure_writable(destination_path)
        shutil.copy2(payload_path, destination_path)
    finally:
        shutil.rmtree(tmpdir_path, ignore_errors=True)

    if logger:
        logger.info("Special K installed to %s", destination_path)
    return True


def install_specialk(
    target_path: str,
    final_dll_name: str,
    module_download_links: Mapping[str, object],
    logger=None,
    cached_archive_path: str = "",
) -> None:
    """Install Special K for side effects only; callers do not use a return value."""
    url = extract_module_url(module_download_links, "specialk")
    installer_services.install_specialk(
        target_path,
        final_dll_name,
        url=url,
        logger=logger,
        cached_archive_path=cached_archive_path,
    )
    if logger:
        source = cached_archive_path or url
        logger.info("Installed Special K from %s to %s", source, target_path)
