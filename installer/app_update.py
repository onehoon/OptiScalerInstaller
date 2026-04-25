from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from tkinter import messagebox
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse
import webbrowser
import zipfile

from .common.log_sanitizer import redact_text
from .common.update_launch import build_updated_installer_launch_command
from .i18n import AppStrings
from .install import services as installer_services


INSTALLER_LATEST_RELEASE_URL = "https://github.com/onehoon/OptiScalerInstaller/releases/latest"


def parse_version_tuple(verstr: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(verstr or "")))


def get_runtime_launch_path() -> Path:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
            return Path(sys.executable).resolve()
    except Exception:
        pass
    return Path(__file__).resolve()


def get_runtime_install_dir() -> Path:
    return get_runtime_launch_path().parent


def build_expected_installer_exe_name(version_text: str, fallback_url: str = "") -> str:
    normalized = re.sub(r"\s+", "", str(version_text or ""))
    if normalized.lower().endswith(".exe"):
        return Path(normalized).name
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    if normalized:
        return f"OptiScaler_Installer_v{normalized}.exe"

    fallback_name = Path(urlparse(str(fallback_url or "")).path).name
    if fallback_name.lower().endswith(".exe"):
        return fallback_name
    return ""


def resolve_safe_child_path(base_dir: Path, child_path: str) -> Optional[Path]:
    raw_name = str(child_path or "").replace("\\", "/").strip()
    if not raw_name:
        return None

    try:
        resolved_base = base_dir.resolve(strict=False)
        resolved_child = (resolved_base / Path(raw_name)).resolve(strict=False)
        resolved_child.relative_to(resolved_base)
        return resolved_child
    except Exception:
        return None


def get_installer_update_entry(module_download_links: Mapping[str, object]) -> dict:
    if not isinstance(module_download_links, Mapping):
        return {}
    entry = module_download_links.get("latest_installer_dl", {})
    if not isinstance(entry, dict) or not entry:
        entry = module_download_links.get("optiscaler_installer", {})
    return entry if isinstance(entry, dict) else {}


def prepare_installer_update_payload(payload_path: Path, target_dir: Path, latest_version: str) -> Path:
    payload_ext = payload_path.suffix.lower()
    expected_exe_name = build_expected_installer_exe_name(latest_version, str(payload_path))

    if payload_ext == ".exe":
        if expected_exe_name and payload_path.name.lower() != expected_exe_name.lower():
            renamed_target = payload_path.with_name(expected_exe_name)
            if renamed_target.exists():
                try:
                    renamed_target.unlink()
                except Exception:
                    logging.debug(
                        "Failed to remove existing installer payload before rename: %s",
                        renamed_target,
                        exc_info=True,
                    )
            payload_path.replace(renamed_target)
            payload_path = renamed_target
        logging.info("[APP] Downloaded updated installer executable to %s", payload_path)
        return payload_path

    if payload_ext != ".zip":
        raise ValueError(f"Unsupported installer update payload: {payload_path}")

    exe_members: list[str] = []
    with zipfile.ZipFile(payload_path, "r") as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_name = str(member.filename).replace("\\", "/").strip()
            if member_name.lower().endswith(".exe"):
                exe_members.append(member_name)

    try:
        installer_services.extract_archive(str(payload_path), str(target_dir))
        launch_candidates: list[Path] = []
        for member_name in exe_members:
            candidate = resolve_safe_child_path(target_dir, member_name)
            if candidate and candidate.exists():
                launch_candidates.append(candidate)

        if expected_exe_name:
            for candidate in launch_candidates:
                if candidate.name.lower() == expected_exe_name.lower():
                    logging.info("[APP] Prepared updated installer from zip: %s", candidate)
                    return candidate

            direct_expected = target_dir / expected_exe_name
            if direct_expected.exists():
                logging.info("[APP] Prepared updated installer from zip: %s", direct_expected)
                return direct_expected

        if len(launch_candidates) == 1:
            logging.info("[APP] Prepared updated installer from zip: %s", launch_candidates[0])
            return launch_candidates[0]

        if not launch_candidates:
            raise FileNotFoundError(f"No installer executable found in update zip: {payload_path}")

        raise RuntimeError(
            "Multiple installer executables were extracted from update zip and no unique target could be selected."
        )
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except Exception:
            logging.debug("Failed to remove installer update zip after extraction: %s", payload_path, exc_info=True)


class InstallerUpdateManager:
    def __init__(
        self,
        root,
        *,
        current_version: str,
        strings: AppStrings,
        on_busy_state_changed: Optional[Callable[[], None]] = None,
        on_update_failed: Optional[Callable[[], None]] = None,
        on_exit_requested: Optional[Callable[[], None]] = None,
    ) -> None:
        self.root = root
        self.current_version = str(current_version or "")
        self._strings = strings
        self._on_busy_state_changed = on_busy_state_changed
        self._on_update_failed = on_update_failed
        self._on_exit_requested = on_exit_requested
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="app-update")
        self._in_progress = False

    @property
    def in_progress(self) -> bool:
        return self._in_progress

    def shutdown(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def check_for_update(self, module_download_links: Mapping[str, object], *, blocked: bool = False) -> bool:
        """Check for app update using latest_installer_dl from module_download_links."""
        if blocked:
            return False
        try:
            latest_info = get_installer_update_entry(module_download_links)
            if latest_info:
                app_ver = parse_version_tuple(self.current_version)
                sheet_ver = parse_version_tuple(latest_info.get("version", ""))
                if sheet_ver and app_ver and app_ver < sheet_ver:
                    latest_version = str(latest_info.get("version", "")).strip()
                    if not self._confirm_update(latest_version):
                        logging.info("[APP] User declined installer update to v%s", latest_version)
                        return False
                    self._open_latest_release_page()
                    return self.start_update(latest_info)
        except Exception as exc:
            logging.warning("[APP] Version check failed: %s", redact_text(exc))
        return False

    def start_update(self, latest_info: Mapping[str, object]) -> bool:
        if self._in_progress:
            return True

        latest_version = str(latest_info.get("version", "")).strip()
        download_url = str(latest_info.get("url") or latest_info.get("link") or "").strip()
        if not latest_version or not download_url:
            logging.warning(
                "[APP] Skipping installer update: missing latest_installer_dl metadata (version=%r, has_url=%s)",
                latest_version,
                bool(download_url),
            )
            return False

        source_name = Path(urlparse(download_url).path).name
        source_ext = Path(source_name).suffix.lower()
        if source_ext not in {".zip", ".exe"}:
            logging.warning(
                "[APP] Skipping installer update: unsupported asset type %r",
                source_ext or "<none>",
            )
            return False

        runtime_dir = get_runtime_install_dir()
        if source_ext == ".exe":
            target_name = build_expected_installer_exe_name(latest_version, download_url) or source_name
        else:
            target_name = source_name or "OptiScaler_Installer_update.zip"
        download_path = runtime_dir / Path(target_name).name

        self._set_in_progress(True)
        logging.info("[APP] Starting installer self-update to version %s", latest_version)
        self._executor.submit(
            self._update_worker,
            latest_version,
            download_url,
            str(download_path),
            str(runtime_dir),
        )
        return True

    def _set_in_progress(self, value: bool) -> None:
        if self._in_progress == bool(value):
            return
        self._in_progress = bool(value)
        if callable(self._on_busy_state_changed):
            self._on_busy_state_changed()

    def _confirm_update(self, latest_version: str) -> bool:
        title = self._strings.update.available_title
        detail = self._strings.update.available_body_template.format(version=latest_version)
        return bool(messagebox.askyesno(title, detail))

    def _open_latest_release_page(self) -> None:
        try:
            logging.info("[APP] Opening latest installer release page before self-update")
            opened = webbrowser.open(INSTALLER_LATEST_RELEASE_URL)
            if not opened:
                logging.warning(
                    "[APP] Browser reported failure while opening latest release page: %s",
                    INSTALLER_LATEST_RELEASE_URL,
                )
        except Exception:
            logging.exception("[APP] Failed to open latest installer release page")

    def _update_worker(self, latest_version: str, download_url: str, download_path: str, runtime_dir: str) -> None:
        try:
            payload_path = Path(download_path)
            target_dir = Path(runtime_dir)
            installer_services.download_to_file(download_url, str(payload_path), timeout=300)
            launch_path = prepare_installer_update_payload(payload_path, target_dir, latest_version)
            self.root.after(
                0,
                lambda path=str(launch_path), version=latest_version: self._on_update_ready(path, version, None),
            )
        except Exception as exc:
            logging.error("[APP] Installer self-update failed: %s", redact_text(exc))
            self.root.after(
                0,
                lambda err=str(exc), version=latest_version: self._on_update_ready("", version, err),
            )

    def _launch_updated_installer(self, launch_path: str, latest_version: str) -> None:
        target = Path(launch_path)
        if not target.exists():
            raise FileNotFoundError(f"Updated installer not found: {target}")

        logging.info("[APP] Launching updated installer v%s", latest_version)
        command = build_updated_installer_launch_command(target)
        process = subprocess.Popen(
            command,
            cwd=str(target.parent),
        )
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.AllowSetForegroundWindow(int(process.pid))
            except Exception:
                logging.debug("[APP] Failed to grant foreground permission to updated installer", exc_info=True)

    def _on_update_ready(self, launch_path: str, latest_version: str, error_message: Optional[str]) -> None:
        self._set_in_progress(False)

        if error_message:
            logging.error("[APP] Installer update to v%s failed: %s", latest_version, redact_text(error_message))
            if callable(self._on_update_failed):
                self._on_update_failed()
            return

        try:
            self._launch_updated_installer(launch_path, latest_version)
        except Exception as exc:
            logging.error("[APP] Updated installer launch failed for v%s: %s", latest_version, redact_text(exc))
            if callable(self._on_update_failed):
                self._on_update_failed()
            return

        if callable(self._on_exit_requested):
            self.root.after(50, self._on_exit_requested)
