from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .. import services as installer_services
from ..archive_source import resolve_cached_archive_path
from ._link_utils import extract_module_url


OPTISCALER_ASI_NAME = "OptiScaler.asi"
ULTIMATE_ASI_LOADER_DLL_NAME = "dinput8.dll"
_ULTIMATE_ASI_LOADER_SIGNATURE = "ultimate asi loader"


def is_ultimate_asi_loader_dinput8(file_path: Path) -> bool:
    version_info = installer_services.read_windows_version_strings(file_path)
    return any(_ULTIMATE_ASI_LOADER_SIGNATURE in str(value).lower() for value in version_info.values())


def _resolve_ual_representative_name(dll_names: tuple[str, ...]) -> str:
    """Pick the canonical UAL filename from a set of detected names.

    dinput8.dll takes priority; otherwise the first name in the sorted list is used.
    """
    for name in dll_names:
        if name.lower() == ULTIMATE_ASI_LOADER_DLL_NAME:
            return name
    return sorted(dll_names, key=str.lower)[0] if dll_names else ULTIMATE_ASI_LOADER_DLL_NAME


def _resolve_download_archive_path(tmpdir_path: Path, url: str, *, logger=None) -> Path:
    parsed = urlparse(url)
    archive_name = os.path.basename(parsed.path) or "ultimate_asi_loader.zip"
    archive_path = tmpdir_path / archive_name
    installer_services.download_to_file(url, str(archive_path), timeout=60, logger=logger)
    return archive_path


def _extract_ual_payload_dll(archive_path: Path, tmpdir_path: Path, *, logger=None) -> Path:
    extract_path = tmpdir_path / "payload"
    installer_services.extract_archive(str(archive_path), str(extract_path), logger=logger)

    dll_candidates = [
        candidate
        for candidate in extract_path.rglob(ULTIMATE_ASI_LOADER_DLL_NAME)
        if candidate.is_file() and candidate.name.lower() == ULTIMATE_ASI_LOADER_DLL_NAME
    ]
    if not dll_candidates:
        raise FileNotFoundError("dinput8.dll not found inside Ultimate ASI Loader archive")
    if len(dll_candidates) > 1:
        raise RuntimeError("Multiple dinput8.dll files found inside Ultimate ASI Loader archive")
    return dll_candidates[0]


def install_ultimate_asi_loader(
    target_path: str,
    module_download_links: Mapping[str, object] | None = None,
    ual_detected_names: tuple[str, ...] | None = None,
    logger=None,
    cached_archive_path: str = "",
) -> None:
    """Install or update Ultimate ASI Loader.

    When *ual_detected_names* is provided (auto-detect mode):
    - The representative UAL filename is determined (dinput8.dll has priority).
    - After a successful download + extraction, all detected UAL files are removed
      before the new binary is written under the representative filename.
    - If no download link is available, the update is silently skipped.

    When *ual_detected_names* is None (sheet-flag mode):
    - The target DLL is always ``dinput8.dll``.
    - A missing download link raises an error unless a cached archive is available.
    - An existing ``dinput8.dll`` that is not a UAL binary blocks installation.
    """
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    links = module_download_links if isinstance(module_download_links, Mapping) else {}
    url = extract_module_url(links, "ultimateasiloader")
    cached = resolve_cached_archive_path(cached_archive_path)
    use_cache = cached is not None

    if ual_detected_names is not None:
        if not url and not use_cache:
            if logger:
                logger.info(
                    "Ultimate ASI Loader update skipped: no download link configured "
                    "(existing UAL remains in place)"
                )
            return

        representative_name = _resolve_ual_representative_name(ual_detected_names)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            archive_path = cached if cached is not None else _resolve_download_archive_path(tmpdir_path, url, logger=logger)
            payload_dll = _extract_ual_payload_dll(archive_path, tmpdir_path, logger=logger)

            for detected_name in ual_detected_names:
                old_path = target_dir / detected_name
                if old_path.exists() and old_path.is_file():
                    installer_services.ensure_writable(old_path)
                    old_path.unlink()
                    if logger:
                        logger.info("Removed existing UAL file: %s", detected_name)

            destination_path = target_dir / representative_name
            shutil.copy2(payload_dll, destination_path)

        if logger:
            logger.info(
                "Ultimate ASI Loader updated to %s (renamed from dinput8.dll)",
                destination_path,
            )
        return

    existing_dinput8 = target_dir / ULTIMATE_ASI_LOADER_DLL_NAME
    if existing_dinput8.exists():
        if not existing_dinput8.is_file():
            raise RuntimeError(f"Existing {ULTIMATE_ASI_LOADER_DLL_NAME} is not a file: {existing_dinput8}")
        if not is_ultimate_asi_loader_dinput8(existing_dinput8):
            raise RuntimeError(
                "Existing dinput8.dll does not appear to be Ultimate ASI Loader. "
                "Installation was stopped to avoid overwriting another mod or loader."
            )
        installer_services.ensure_writable(existing_dinput8)

    if not url and not use_cache:
        raise FileNotFoundError("Ultimate ASI Loader download link is not configured")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        archive_path = cached if cached is not None else _resolve_download_archive_path(tmpdir_path, url, logger=logger)
        payload_dll = _extract_ual_payload_dll(archive_path, tmpdir_path, logger=logger)

        destination_path = target_dir / ULTIMATE_ASI_LOADER_DLL_NAME
        if destination_path.exists():
            installer_services.ensure_writable(destination_path)
        shutil.copy2(payload_dll, destination_path)

    if logger:
        logger.info("Ultimate ASI Loader dinput8.dll installed to %s", destination_path)
