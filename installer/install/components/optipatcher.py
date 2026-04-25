from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .. import services as installer_services
from ..archive_source import resolve_cached_archive_path
from ._link_utils import extract_module_url


OPTIPATCHER_PLUGIN_NAME = "OptiPatcher.asi"
OPTIPATCHER_ARCHIVE_EXTENSIONS = {".zip", ".7z"}


def _is_optipatcher_asi_name(file_name: str) -> bool:
    normalized = Path(str(file_name or "").strip()).name.lower()
    return normalized.endswith(".asi") and "optipatcher" in normalized


def _select_single_optipatcher_payload(candidates: list[Path], extract_dir: Path) -> Path | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    exact_matches = [
        candidate
        for candidate in candidates
        if candidate.name.lower() == OPTIPATCHER_PLUGIN_NAME.lower()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    normalized_candidates = ", ".join(
        sorted(str(candidate.relative_to(extract_dir)).replace("\\", "/") for candidate in candidates)
    )
    raise RuntimeError(
        "Multiple OptiPatcher payload candidates were found inside the archive: "
        f"{normalized_candidates}"
    )


def _resolve_optipatcher_download_name(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    file_name = os.path.basename(parsed.path).strip()
    return file_name or OPTIPATCHER_PLUGIN_NAME


def _resolve_optipatcher_payload(download_path: Path, extract_dir: Path, logger=None) -> Path:
    if download_path.suffix.lower() not in OPTIPATCHER_ARCHIVE_EXTENSIONS:
        return download_path

    installer_services.extract_archive(str(download_path), str(extract_dir), logger=logger)
    preferred_candidates = [
        candidate
        for candidate in extract_dir.rglob("*")
        if candidate.is_file() and _is_optipatcher_asi_name(candidate.name)
    ]
    payload_path = _select_single_optipatcher_payload(preferred_candidates, extract_dir)
    if payload_path is not None:
        return payload_path

    fallback_candidates = [
        candidate
        for candidate in extract_dir.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() == ".asi"
    ]
    payload_path = _select_single_optipatcher_payload(fallback_candidates, extract_dir)
    if payload_path is not None:
        return payload_path

    raise FileNotFoundError("OptiPatcher .asi payload was not found inside the downloaded archive")


def _remove_existing_optipatcher_plugins(plugins_dir: Path, logger=None) -> None:
    if not plugins_dir.is_dir():
        return

    for plugin_path in sorted(
        (
            child
            for child in plugins_dir.iterdir()
            if child.is_file() and _is_optipatcher_asi_name(child.name)
        ),
        key=lambda path: path.name.lower(),
    ):
        installer_services.ensure_writable(plugin_path)
        try:
            plugin_path.unlink()
        except OSError as exc:
            message = f"Failed to remove existing OptiPatcher plugin: {plugin_path.name}"
            if logger:
                logger.error("%s (%s)", message, exc)
            else:
                logging.error("%s (%s)", message, exc)
            raise RuntimeError(message) from exc

        message = f"Removed existing OptiPatcher plugin: {plugin_path.name}"
        if logger:
            logger.info(message)
        else:
            logging.info(message)


def install_optipatcher_payload(target_path, url, logger=None, cached_archive_path=""):
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    plugins_dir = target_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    destination_path = plugins_dir / OPTIPATCHER_PLUGIN_NAME

    cached = resolve_cached_archive_path(cached_archive_path)
    use_cache = cached is not None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        if use_cache:
            download_path = cached
        else:
            download_name = _resolve_optipatcher_download_name(url)
            download_path = tmpdir_path / download_name
            installer_services.download_to_file(url, str(download_path), timeout=30, logger=logger)

        extract_dir = tmpdir_path / "payload"
        payload_path = _resolve_optipatcher_payload(download_path, extract_dir, logger=logger)

        _remove_existing_optipatcher_plugins(plugins_dir, logger=logger)

        if destination_path.exists():
            installer_services.ensure_writable(destination_path)
        shutil.copy2(payload_path, destination_path)


def install_optipatcher(
    target_path: str,
    game_data: Mapping[str, object],
    module_download_links: Mapping[str, object],
    logger=None,
    cached_archive_path: str = "",
) -> None:
    """Install OptiPatcher files only."""
    if not bool(game_data.get("optipatcher")):
        return

    opti_url = extract_module_url(module_download_links, "optipatcher")
    if not (opti_url or cached_archive_path):
        return

    install_optipatcher_payload(target_path, url=opti_url, logger=logger, cached_archive_path=cached_archive_path)
    if logger:
        if cached_archive_path:
            logger.info("Installed OptiPatcher from cached archive %s to %s", cached_archive_path, target_path)
        else:
            logger.info("Installed OptiPatcher from %s to %s", opti_url, target_path)
