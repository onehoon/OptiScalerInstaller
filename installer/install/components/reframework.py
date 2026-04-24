from __future__ import annotations

from typing import Mapping

from .. import services as installer_services


def install_reframework_dinput8(target_path: str, game_data: Mapping[str, object], logger=None) -> bool:
    """Return True only when REFramework dinput8.dll was requested and installed."""
    url = str(game_data.get("reframework_url", "") or "").strip()
    if not url:
        return False

    installer_services.install_reframework_dinput8_from_url(url, target_path, logger=logger)
    if logger:
        logger.info("Installed REFramework dinput8.dll from %s to %s", url, target_path)
    return True
