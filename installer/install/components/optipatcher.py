from __future__ import annotations

from typing import Mapping

from .. import services as installer_services
from ._link_utils import extract_module_url


def install_optipatcher(
    target_path: str,
    game_data: Mapping[str, object],
    module_download_links: Mapping[str, object],
    logger=None,
    cached_archive_path: str = "",
) -> dict[str, str]:
    """Install OptiPatcher and return OptiScaler INI overrides to merge."""
    if not bool(game_data.get("optipatcher")):
        return {}

    opti_url = extract_module_url(module_download_links, "optipatcher")
    if not (opti_url or cached_archive_path):
        return {}

    installer_services.install_optipatcher(target_path, url=opti_url, logger=logger, cached_archive_path=cached_archive_path)
    if logger:
        if cached_archive_path:
            logger.info("Installed OptiPatcher from cached archive %s to %s", cached_archive_path, target_path)
        else:
            logger.info("Installed OptiPatcher from %s to %s", opti_url, target_path)
    return {"LoadAsiPlugins": "True"}
