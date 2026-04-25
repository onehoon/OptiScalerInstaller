from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .. import services as installer_services
from ..archive_source import resolve_cached_archive_path
from ._link_utils import extract_module_url


def install_unreal5_payload(url, target_path, logger=None, cached_archive_path=""):
    cached = resolve_cached_archive_path(cached_archive_path)
    use_cache = cached is not None

    if not use_cache:
        parsed = urlparse(url)
        file_name = os.path.basename(parsed.path)
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in {".zip", ".7z"}:
            msg = "Unreal5 URL must point to .zip or .7z archive"
            if logger:
                logger.error(msg)
            raise ValueError(msg)

    if installer_services.target_has_filename(target_path, "dxgi.dll"):
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        if use_cache:
            archive_path = str(cached)
        else:
            archive_path = str(Path(tmpdir) / (file_name or f"unreal5_patch{ext}"))
            installer_services.download_to_file(url, archive_path, timeout=60, logger=logger)
        installer_services.extract_archive(archive_path, target_path, logger=logger)
    return True


def install_unreal5_patch(
    target_path: str,
    game_data: Mapping[str, object],
    module_download_links: Mapping[str, object],
    logger=None,
    cached_archive_path: str = "",
) -> bool:
    """Return True when the Unreal5 patch archive was actually installed."""
    if not bool(game_data.get("unreal5")):
        return False

    unreal_url = extract_module_url(module_download_links, "unreal5")
    if not (unreal_url or cached_archive_path):
        return False

    unreal_installed = bool(installer_services.install_unreal5_from_url(
        unreal_url, target_path, logger=logger, cached_archive_path=cached_archive_path
    ))
    if logger:
        if unreal_installed:
            logger.info("Installed Unreal5 patch")
        else:
            logger.info("Skipped Unreal5 patch because dxgi.dll is already present in %s", target_path)
    return unreal_installed
