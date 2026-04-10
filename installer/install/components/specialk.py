from __future__ import annotations

from typing import Mapping

from .. import services as installer_services


def install_specialk(
    target_path: str,
    final_dll_name: str,
    module_download_links: Mapping[str, object],
    logger=None,
    cached_archive_path: str = "",
    existing_prepared: bool = False,
) -> None:
    link_entry = module_download_links.get("specialk")
    url = ""
    if isinstance(link_entry, dict):
        url = str(link_entry.get("url", "") or "").strip()

    installed = installer_services.install_specialk(
        target_path,
        final_dll_name,
        url=url,
        logger=logger,
        cached_archive_path=cached_archive_path,
        existing_prepared=existing_prepared,
    )
    if logger and installed:
        source = cached_archive_path or url
        logger.info("Installed Special K from %s to %s", source, target_path)
