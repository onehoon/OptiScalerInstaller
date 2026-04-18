from __future__ import annotations

from typing import Mapping

from .. import services as installer_services


def install_unreal5_patch(
    target_path: str,
    game_data: Mapping[str, object],
    module_download_links: Mapping[str, object],
    gpu_info,
    logger=None,
    cached_archive_path: str = "",
) -> bool:
    if not bool(game_data.get("unreal5")):
        return False

    unreal_link_entry = module_download_links.get("unreal5")
    unreal_url = ""
    if isinstance(unreal_link_entry, dict) and unreal_link_entry.get("url"):
        unreal_url = str(unreal_link_entry["url"]).strip()

    if not (unreal_url or cached_archive_path):
        return False

    unreal_installed = bool(installer_services.install_unreal5_from_url(
        unreal_url, target_path, logger=logger, cached_archive_path=cached_archive_path
    ))
    if logger:
        if unreal_installed:
            logger.info("Installed Unreal5 patch from %s to %s", cached_archive_path or unreal_url, target_path)
        else:
            logger.info("Skipped Unreal5 patch because dxgi.dll is already present in %s", target_path)
    return unreal_installed
