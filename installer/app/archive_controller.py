from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import zipfile

from ..common.download_manifest import is_update_needed, write_manifest_entry


SchedulerCallback = Callable[[Callable[[], None]], Any]
DownloadToFile = Callable[..., None]
ArchiveStateCallback = Callable[["ArchivePreparationState"], None]

_ARCHIVE_SUFFIXES = {".7z", ".zip", ".rar", ".tar", ".gz", ".xz", ".bz2", ".asi"}


@dataclass(frozen=True)
class ArchivePreparationState:
    filename: str
    archive_path: str
    ready: bool
    downloading: bool
    error_message: str


@dataclass(frozen=True)
class ArchivePreparationCallbacks:
    on_optiscaler_state_changed: ArchiveStateCallback
    on_fsr4_state_changed: ArchiveStateCallback
    on_optipatcher_state_changed: ArchiveStateCallback
    on_specialk_state_changed: ArchiveStateCallback
    on_ual_state_changed: ArchiveStateCallback
    on_unreal5_state_changed: ArchiveStateCallback


class ArchivePreparationController:
    def __init__(
        self,
        *,
        executor: Executor,
        schedule: SchedulerCallback,
        callbacks: ArchivePreparationCallbacks,
        download_to_file: DownloadToFile,
        manifest_root: Path | None = None,
        logger=None,
    ) -> None:
        self._executor = executor
        self._schedule = schedule
        self._callbacks = callbacks
        self._download_to_file = download_to_file
        self._manifest_root = manifest_root
        self._logger = logger or logging.getLogger()

    def prepare_optiscaler(self, entry: Mapping[str, object] | None, cache_dir: Path) -> ArchivePreparationState:
        normalized_entry = self._normalize_entry(entry)
        url = str(normalized_entry.get("url", "")).strip()
        filename = self._resolve_archive_filename(normalized_entry)

        if not url or not filename:
            self._logger.warning(
                "[APP] OptiScaler archive preparation skipped: missing metadata (url=%r, filename=%r, entry=%r)",
                url,
                filename,
                normalized_entry,
            )
            return ArchivePreparationState(
                filename=filename,
                archive_path="",
                ready=False,
                downloading=False,
                error_message="Missing archive metadata in sheet.",
            )

        cache_path = cache_dir / filename
        if cache_path.exists():
            if cache_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(cache_path):
                self._logger.warning("[APP] Cached OptiScaler file is invalid, removing and downloading again: %s", cache_path)
                try:
                    cache_path.unlink()
                except OSError as exc:
                    return ArchivePreparationState(
                        filename=filename,
                        archive_path="",
                        ready=False,
                        downloading=False,
                        error_message=f"Failed to remove invalid OptiScaler cache: {exc}",
                    )
            else:
                self._logger.info("[APP] OptiScaler archive already cached: %s", cache_path)
                self._cleanup_stale_archives(cache_dir, filename, label="OptiScaler archive cache")
                return ArchivePreparationState(
                    filename=filename,
                    archive_path=str(cache_path),
                    ready=True,
                    downloading=False,
                    error_message="",
                )

        self._logger.info("[APP] Starting OptiScaler archive download: %s -> %s", url, cache_path)
        return self._start_download(
            asset_key="optiscaler",
            asset_label="OptiScaler archive",
            url=url,
            cache_dir=cache_dir,
            cache_path=cache_path,
            filename=filename,
            validate_zip=True,
            cleanup_stale=True,
        )

    def prepare_fsr4(
        self,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        *,
        enabled: bool,
    ) -> ArchivePreparationState:
        if not enabled:
            return ArchivePreparationState(
                filename="",
                archive_path="",
                ready=False,
                downloading=False,
                error_message="",
            )

        normalized_entry = self._normalize_entry(entry)
        url = str(normalized_entry.get("url", "")).strip()
        filename = self._resolve_archive_filename(normalized_entry)

        if not url or not filename:
            self._logger.warning(
                "[APP] FSR4 preparation skipped: missing metadata (filename=%r, entry=%r)",
                filename,
                normalized_entry,
            )
            return ArchivePreparationState(
                filename=filename,
                archive_path="",
                ready=False,
                downloading=False,
                error_message="Missing FSR4 download metadata in sheet.",
            )

        cache_path = cache_dir / filename
        if cache_path.exists():
            if cache_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(cache_path):
                self._logger.warning("[APP] Cached FSR4 file is invalid, removing and downloading again: %s", cache_path)
                try:
                    cache_path.unlink()
                except OSError as exc:
                    return ArchivePreparationState(
                        filename=filename,
                        archive_path="",
                        ready=False,
                        downloading=False,
                        error_message=f"Failed to remove invalid FSR4 cache: {exc}",
                    )
            else:
                self._logger.info("[APP] FSR4 already cached: %s", cache_path)
                return ArchivePreparationState(
                    filename=filename,
                    archive_path=str(cache_path),
                    ready=True,
                    downloading=False,
                    error_message="",
                )

        self._logger.info("[APP] Starting FSR4 download: %s", filename)
        return self._start_download(
            asset_key="fsr4",
            asset_label="FSR4 archive",
            url=url,
            cache_dir=cache_dir,
            cache_path=cache_path,
            filename=filename,
            validate_zip=True,
            cleanup_stale=True,
        )

    def prepare_optipatcher(
        self,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        manifest_root: Path | None = None,
    ) -> ArchivePreparationState:
        return self._prepare_versioned_asset(
            entry=entry,
            cache_dir=cache_dir,
            manifest_root=manifest_root or self._manifest_root,
            asset_key="optipatcher",
            asset_label="OptiPatcher archive",
        )

    def prepare_specialk(
        self,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        manifest_root: Path | None = None,
    ) -> ArchivePreparationState:
        return self._prepare_versioned_asset(
            entry=entry,
            cache_dir=cache_dir,
            manifest_root=manifest_root or self._manifest_root,
            asset_key="specialk",
            asset_label="Special K archive",
        )

    def prepare_ual(
        self,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        manifest_root: Path | None = None,
    ) -> ArchivePreparationState:
        return self._prepare_versioned_asset(
            entry=entry,
            cache_dir=cache_dir,
            manifest_root=manifest_root or self._manifest_root,
            asset_key="ultimateasiloader",
            asset_label="Ultimate ASI Loader archive",
        )

    def prepare_unreal5(
        self,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        manifest_root: Path | None = None,
    ) -> ArchivePreparationState:
        return self._prepare_versioned_asset(
            entry=entry,
            cache_dir=cache_dir,
            manifest_root=manifest_root or self._manifest_root,
            asset_key="unreal5",
            asset_label="Unreal5 patch archive",
        )

    def _prepare_versioned_asset(
        self,
        *,
        entry: Mapping[str, object] | None,
        cache_dir: Path,
        manifest_root: Path | None,
        asset_key: str,
        asset_label: str,
    ) -> ArchivePreparationState:
        normalized_entry = self._normalize_entry(entry)
        url = str(normalized_entry.get("url", "")).strip()
        version = str(normalized_entry.get("version", "")).strip()
        filename = self._resolve_archive_filename(normalized_entry)

        if not url or not filename:
            self._logger.warning(
                "[APP] %s preparation skipped: missing metadata (url=%r, filename=%r)",
                asset_label, url, filename,
            )
            return ArchivePreparationState(
                filename=filename,
                archive_path="",
                ready=False,
                downloading=False,
                error_message=f"Missing {asset_label} download metadata in sheet.",
            )

        cache_path = cache_dir / filename
        if cache_path.exists():
            update_needed = (
                is_update_needed(manifest_root, asset_key, version)
                if manifest_root and version
                else False
            )
            if update_needed:
                self._logger.info(
                    "[APP] %s version changed, removing stale cache: %s", asset_label, cache_path
                )
                try:
                    cache_path.unlink()
                except OSError as exc:
                    return ArchivePreparationState(
                        filename=filename,
                        archive_path="",
                        ready=False,
                        downloading=False,
                        error_message=f"Failed to remove stale {asset_label} cache: {exc}",
                    )
                # Also remove any other stale archives (handles filename changes between versions)
                self._cleanup_stale_archives(cache_dir, filename, label=f"{asset_label} cache")
            else:
                if cache_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(cache_path):
                    self._logger.warning(
                        "[APP] Cached %s file is invalid, removing and downloading again: %s",
                        asset_label, cache_path,
                    )
                    try:
                        cache_path.unlink()
                    except OSError as exc:
                        return ArchivePreparationState(
                            filename=filename,
                            archive_path="",
                            ready=False,
                            downloading=False,
                            error_message=f"Failed to remove invalid {asset_label} cache: {exc}",
                        )
                else:
                    self._logger.info("[APP] %s already cached: %s", asset_label, cache_path)
                    return ArchivePreparationState(
                        filename=filename,
                        archive_path=str(cache_path),
                        ready=True,
                        downloading=False,
                        error_message="",
                    )
        else:
            # cache_path doesn't exist: still clean up any stale archives from previous versions
            self._cleanup_stale_archives(cache_dir, filename, label=f"{asset_label} cache")

        self._logger.info("[APP] Starting %s download: %s", asset_label, filename)
        return self._start_download(
            asset_key=asset_key,
            asset_label=asset_label,
            url=url,
            cache_dir=cache_dir,
            cache_path=cache_path,
            filename=filename,
            validate_zip=True,
            cleanup_stale=False,
            version=version,
            manifest_root=manifest_root,
        )

    def _start_download(
        self,
        *,
        asset_key: str,
        asset_label: str,
        url: str,
        cache_dir: Path,
        cache_path: Path,
        filename: str,
        validate_zip: bool,
        cleanup_stale: bool,
        version: str = "",
        manifest_root: Path | None = None,
    ) -> ArchivePreparationState:
        try:
            self._executor.submit(
                self._run_download_worker,
                asset_key,
                asset_label,
                url,
                cache_dir,
                cache_path,
                filename,
                validate_zip,
                cleanup_stale,
                version,
                manifest_root,
            )
        except Exception as exc:
            self._logger.exception("Failed to submit %s download worker", asset_label)
            return ArchivePreparationState(
                filename=filename,
                archive_path="",
                ready=False,
                downloading=False,
                error_message=str(exc),
            )

        return ArchivePreparationState(
            filename=filename,
            archive_path=str(cache_path),
            ready=False,
            downloading=True,
            error_message="",
        )

    def _run_download_worker(
        self,
        asset_key: str,
        asset_label: str,
        url: str,
        cache_dir: Path,
        cache_path: Path,
        filename: str,
        validate_zip: bool,
        cleanup_stale: bool,
        version: str = "",
        manifest_root: Path | None = None,
    ) -> None:
        try:
            self._download_to_file(url, str(cache_path), timeout=300)
            if validate_zip and cache_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(cache_path):
                cache_path.unlink(missing_ok=True)
                raise RuntimeError(f"Downloaded {asset_label} file is not a valid zip file: {cache_path}")

            self._logger.info("[APP] %s download completed: %s", asset_label, cache_path)
            if cleanup_stale:
                self._cleanup_stale_archives(cache_dir, filename, label="OptiScaler archive cache")
            if manifest_root and version:
                try:
                    write_manifest_entry(manifest_root, asset_key, version)
                except Exception:
                    self._logger.debug("[APP] Failed to write manifest entry for %s", asset_key, exc_info=True)
            state = ArchivePreparationState(
                filename=filename,
                archive_path=str(cache_path),
                ready=True,
                downloading=False,
                error_message="",
            )
        except Exception as exc:
            self._logger.error("[APP] %s download failed: %s", asset_label, exc)
            state = ArchivePreparationState(
                filename=filename,
                archive_path="",
                ready=False,
                downloading=False,
                error_message=str(exc),
            )

        self._schedule_state_change(asset_key, state, description=f"{asset_label} completion callback")

    def _schedule_state_change(self, asset_key: str, state: ArchivePreparationState, *, description: str) -> None:
        try:
            self._schedule(
                lambda scheduled_state=state, scheduled_asset=asset_key: self._emit_state_change(
                    scheduled_asset,
                    scheduled_state,
                )
            )
        except Exception:
            self._logger.exception("Failed to schedule %s", description)

    def _emit_state_change(self, asset_key: str, state: ArchivePreparationState) -> None:
        if asset_key == "optiscaler":
            self._callbacks.on_optiscaler_state_changed(state)
            return
        if asset_key == "fsr4":
            self._callbacks.on_fsr4_state_changed(state)
            return
        if asset_key == "optipatcher":
            self._callbacks.on_optipatcher_state_changed(state)
            return
        if asset_key == "specialk":
            self._callbacks.on_specialk_state_changed(state)
            return
        if asset_key in ("ual", "ultimateasiloader"):
            self._callbacks.on_ual_state_changed(state)
            return
        if asset_key == "unreal5":
            self._callbacks.on_unreal5_state_changed(state)
            return
        self._logger.warning("Unknown archive asset key: %s", asset_key)

    def _cleanup_stale_archives(self, cache_dir: Path, keep_filename: str, *, label: str) -> None:
        for stale_path in self._list_stale_archive_paths(cache_dir, keep_filename):
            try:
                stale_path.unlink()
                self._logger.info("[APP] Removed stale %s: %s", label, stale_path)
            except OSError:
                self._logger.warning("[APP] Failed to remove stale %s: %s", label, stale_path, exc_info=True)

    def _list_stale_archive_paths(self, cache_dir: Path, keep_filename: str) -> list[Path]:
        if not cache_dir.exists():
            return []

        keep_name = Path(str(keep_filename or "")).name.casefold()
        stale_paths: list[Path] = []
        for cache_path in cache_dir.iterdir():
            if not cache_path.is_file():
                continue
            if keep_name and cache_path.name.casefold() == keep_name:
                continue
            if cache_path.suffix.lower() not in _ARCHIVE_SUFFIXES:
                continue
            stale_paths.append(cache_path)
        return sorted(stale_paths)

    def _resolve_archive_filename(self, entry: Mapping[str, object]) -> str:
        filename = str(entry.get("filename", "") or entry.get("version", "")).strip()
        if filename:
            return Path(filename).name

        url = str(entry.get("url", "")).strip()
        if not url:
            return ""
        return Path(urlparse(url).path).name

    @staticmethod
    def _normalize_entry(entry: Mapping[str, object] | None) -> dict[str, object]:
        return dict(entry) if isinstance(entry, Mapping) else {}
