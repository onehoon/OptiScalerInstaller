from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import Mapping
from urllib.parse import urlparse

from .. import services as installer_services


def install_reframework_dinput8_payload(url, target_path, logger=None):
    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path) or "reframework.zip"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = str(os.path.join(tmpdir, file_name))
        installer_services.download_to_file(url, archive_path, timeout=60, logger=logger)

        with zipfile.ZipFile(archive_path, "r") as z:
            dll_member = next(
                (
                    m for m in z.namelist()
                    if not m.endswith("/") and os.path.basename(m).lower() == "dinput8.dll"
                ),
                None,
            )

            if not dll_member:
                msg = "dinput8.dll not found inside reframework zip"
                if logger:
                    logger.error(msg)
                raise FileNotFoundError(msg)

            dst = os.path.join(target_path, "dinput8.dll")
            with z.open(dll_member, "r") as src_fp, open(dst, "wb") as dst_fp:
                shutil.copyfileobj(src_fp, dst_fp)


def install_reframework_dinput8(target_path: str, game_data: Mapping[str, object], logger=None) -> bool:
    """Return True only when REFramework dinput8.dll was requested and installed."""
    url = str(game_data.get("reframework_url", "") or "").strip()
    if not url:
        return False

    installer_services.install_reframework_dinput8_from_url(url, target_path, logger=logger)
    if logger:
        logger.info("Installed REFramework dinput8.dll from %s to %s", url, target_path)
    return True
