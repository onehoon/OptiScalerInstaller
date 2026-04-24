from __future__ import annotations

from typing import Mapping

from .. import services as installer_services
from ._link_utils import extract_module_url


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
