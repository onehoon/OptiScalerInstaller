import fnmatch
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
import ctypes
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse

from ..common.network_utils import get_shared_retry_session
from ..common.process_utils import subprocess_no_window_kwargs

try:
    import py7zr
except ImportError:  # pragma: no cover - optional dependency at runtime
    py7zr = None


OPTISCALER_DLL = "OptiScaler.dll"
OPTISCALER_PROXY_DLL_NAMES = {
    "dxgi.dll",
    "winmm.dll",
    "d3d12.dll",
    "dbghelp.dll",
    "version.dll",
    "wininet.dll",
    "winhttp.dll",
}
OPTISCALER_BACKUP_DLL_NAMES = (
    OPTISCALER_DLL,
    "OptiScaler.asi",
    *tuple(sorted(OPTISCALER_PROXY_DLL_NAMES, key=str.lower)),
)
# Legacy OptiScaler-side compatibility cleanup targets.
# These names are intentionally removed as stale OptiScaler artifacts, not treated as third-party mods.
OPTISCALER_LEGACY_REMOVE_NAMES = {
    "nvapi64.dll",
    "nvngx.dll",
}
OPTISCALER_PROXY_FALLBACK_NAMES = ("winmm.dll", "version.dll")
RESHADE_COMPAT_INSTALL_ENABLED = False
SPECIALK_AUTO_DETECT_INSTALL_ENABLED = False
OPTIPATCHER_PLUGIN_NAME = "OptiPatcher.asi"
OPTIPATCHER_ARCHIVE_EXTENSIONS = {".zip", ".7z"}
SPECIALK64_DLL_NAME = "SpecialK64.dll"
SPECIALK_ARCHIVE_EXTENSIONS = {".zip", ".7z"}

_file_session = get_shared_retry_session()


def _normalize_rel_path(rel_path: str) -> str:
    normalized = str(rel_path or "").replace("\\", "/").strip()
    if normalized in {"", "."}:
        return ""
    return normalized.strip("/")


def _should_exclude_rel_path(rel_path: str, patterns: list[str]) -> bool:
    normalized_rel_path = _normalize_rel_path(rel_path)
    if not normalized_rel_path:
        return False

    basename = os.path.basename(normalized_rel_path)
    for pattern in patterns:
        normalized_pattern = _normalize_rel_path(pattern)
        if not normalized_pattern:
            continue
        if "/" in normalized_pattern:
            if fnmatch.fnmatch(normalized_rel_path, normalized_pattern):
                return True
            if normalized_pattern.endswith("/*"):
                dir_prefix = normalized_pattern[:-2].strip("/")
                if dir_prefix and (normalized_rel_path == dir_prefix or normalized_rel_path.startswith(dir_prefix + "/")):
                    return True
        else:
            if fnmatch.fnmatch(basename, normalized_pattern):
                return True
            if fnmatch.fnmatch(normalized_rel_path, normalized_pattern):
                return True
    return False


def _old_opti_backup_path(file_path: Path) -> Path:
    return file_path.with_name(f"old_opti_{file_path.name}")


def _ensure_writable(file_path: Path) -> None:
    try:
        os.chmod(file_path, 0o666)
    except OSError:
        logging.debug("Failed to update file attributes for %s", file_path)


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


def _read_windows_version_strings(file_path: Path) -> dict[str, str]:
    if os.name != "nt":
        return {}

    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(file_path), None)
        if not size:
            return {}

        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(file_path), 0, size, buffer):
            return {}

        trans_ptr = ctypes.c_void_p()
        trans_len = wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\VarFileInfo\\Translation", ctypes.byref(trans_ptr), ctypes.byref(trans_len)):
            return {}
        if trans_len.value < 4:
            return {}

        translation = ctypes.cast(trans_ptr, ctypes.POINTER(ctypes.c_ushort))
        lang, codepage = translation[0], translation[1]

        values = {}
        for key in ("CompanyName", "FileDescription", "ProductName", "OriginalFilename", "InternalName"):
            value_ptr = ctypes.c_wchar_p()
            value_len = wintypes.UINT()
            sub_block = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
            if version.VerQueryValueW(buffer, sub_block, ctypes.byref(value_ptr), ctypes.byref(value_len)) and value_ptr.value:
                values[key] = value_ptr.value.strip()
        return values
    except Exception:
        logging.debug("Failed to read version strings for %s", file_path, exc_info=True)
        return {}


def read_windows_version_strings(file_path) -> dict[str, str]:
    return _read_windows_version_strings(Path(file_path))


def _file_contains_optiscaler_signature(file_path: Path) -> bool:
    try:
        with file_path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    return False
                if b"optiscaler" in chunk.lower():
                    return True
    except Exception:
        logging.debug("Failed to inspect file contents for %s", file_path, exc_info=True)
    return False


def _is_optiscaler_managed_proxy_dll(file_path: Path) -> bool:
    version_info = _read_windows_version_strings(file_path)
    for value in version_info.values():
        if "optiscaler" in value.lower():
            return True
    return _file_contains_optiscaler_signature(file_path)


def is_optiscaler_managed_proxy_dll(file_path) -> bool:
    return _is_optiscaler_managed_proxy_dll(Path(file_path))


def target_has_filename(target_path, file_name: str) -> bool:
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        return False

    desired = str(file_name or "").strip().lower()
    if not desired:
        return False

    try:
        return any(child.is_file() and child.name.lower() == desired for child in target_dir.iterdir())
    except Exception:
        logging.debug("Failed to inspect target directory for %s", file_name, exc_info=True)
        return False


def resolve_proxy_dll_name(target_path, preferred_name="", logger=None, reusable_filenames=None) -> str:
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    reusable_name_set = {
        Path(str(name).strip()).name.lower()
        for name in (reusable_filenames or ())
        if str(name).strip()
    }

    candidates = []
    for name in [preferred_name, *OPTISCALER_PROXY_FALLBACK_NAMES]:
        normalized = str(name or "").strip()
        if not normalized:
            continue
        if normalized.lower() not in {c.lower() for c in candidates}:
            candidates.append(normalized)

    if not candidates:
        raise RuntimeError("No proxy DLL name candidates are available for OptiScaler.")

    for candidate in candidates:
        candidate_path = target_dir / candidate
        if not candidate_path.exists():
            if logger:
                logger.info("OptiScaler DLL name: %s", candidate)
            return candidate
        if candidate_path.name.lower() in reusable_name_set:
            if logger:
                logger.info(
                    "OptiScaler DLL name reserved for planned ReShade migration: %s",
                    candidate,
                )
            return candidate
        if _is_optiscaler_managed_proxy_dll(candidate_path):
            if logger:
                logger.info(
                    "OptiScaler DLL name after planned backup of existing OptiScaler DLL: %s",
                    candidate,
                )
            return candidate
        if logger:
            logger.info("OptiScaler DLL name already in use by non-OptiScaler file, skipping: %s", candidate)

    raise RuntimeError(
        "No available OptiScaler DLL names for installation. "
        f"Checked: {', '.join(candidates)}"
    )


def prepare_reshade_for_optiscaler(target_path, install_mode="", source_dll_name="", logger=None) -> bool:
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    normalized_mode = str(install_mode or "").strip().lower()
    compat_path = target_dir / "ReShade64.dll"

    if normalized_mode in {"", "disabled"}:
        return False
    if not RESHADE_COMPAT_INSTALL_ENABLED:
        if logger:
            logger.info("ReShade compatibility install is disabled; leaving existing ReShade files untouched.")
        return False

    if normalized_mode == "already_migrated":
        if not compat_path.is_file():
            raise RuntimeError(f"Expected migrated ReShade DLL was not found: {compat_path.name}")
        if logger:
            logger.info("ReShade compatibility DLL already present: %s", compat_path.name)
        return True

    if normalized_mode == "invalid_multiple":
        raise RuntimeError("Multiple ReShade DLLs were detected; installation cannot continue.")

    if normalized_mode != "migrate":
        raise RuntimeError(f"Unsupported ReShade install mode: {install_mode}")

    normalized_source_name = Path(str(source_dll_name or "").strip()).name
    if not normalized_source_name:
        raise RuntimeError("ReShade migration requires a source DLL name.")

    source_path = target_dir / normalized_source_name
    if not source_path.is_file():
        raise RuntimeError(f"Expected ReShade DLL was not found: {normalized_source_name}")

    if source_path.name.lower() == compat_path.name.lower():
        if logger:
            logger.info("ReShade compatibility DLL already prepared: %s", compat_path.name)
        return True

    if compat_path.exists():
        raise RuntimeError(f"Cannot migrate ReShade because {compat_path.name} already exists.")

    _ensure_writable(source_path)
    source_path.replace(compat_path)
    if logger:
        logger.info("Migrated ReShade DLL: %s -> %s", normalized_source_name, compat_path.name)
    return True


def prepare_specialk_for_optiscaler(
    target_path,
    final_dll_name="",
    install_mode="",
    source_dll_name="",
    logger=None,
) -> bool:
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    normalized_mode = str(install_mode or "").strip().lower()
    if normalized_mode in {"", "disabled"}:
        return False
    if not SPECIALK_AUTO_DETECT_INSTALL_ENABLED:
        if logger:
            logger.info("Special K auto-detected migration is disabled; leaving existing Special K files untouched.")
        return False

    if normalized_mode != "migrate":
        raise RuntimeError(f"Unsupported Special K install mode: {install_mode}")

    normalized_source_name = Path(str(source_dll_name or "").strip()).name
    normalized_final_name = Path(str(final_dll_name or "").strip()).name
    if not normalized_source_name:
        raise RuntimeError("Special K migration requires a source DLL name.")
    if not normalized_final_name:
        raise RuntimeError("Special K migration requires the final OptiScaler DLL name.")

    source_path = target_dir / normalized_source_name
    if not source_path.is_file():
        raise RuntimeError(f"Expected Special K DLL was not found: {normalized_source_name}")

    plugins_dir = target_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    destination_path = plugins_dir / normalized_final_name

    _ensure_writable(source_path)
    if destination_path.exists():
        if not destination_path.is_file():
            raise RuntimeError(f"Existing Special K plugin destination is not a file: {destination_path}")
        _ensure_writable(destination_path)

    source_path.replace(destination_path)
    if logger:
        logger.info("Moved existing Special K DLL: %s -> %s", normalized_source_name, destination_path)
    return True


def backup_existing_optiscaler_dlls(target_path, logger=None):
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    for dll_name in OPTISCALER_BACKUP_DLL_NAMES:
        dll_path = target_dir / dll_name
        if not dll_path.exists() or not dll_path.is_file():
            continue
        if not _is_optiscaler_managed_proxy_dll(dll_path):
            message = f"Skipped backup for non-OptiScaler DLL cleanup candidate: {dll_path.name}"
            if logger:
                logger.info(message)
            else:
                logging.info(message)
            continue

        backup_path = _old_opti_backup_path(dll_path)
        _ensure_writable(dll_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path.exists():
            if not backup_path.is_file():
                raise RuntimeError(f"Existing OptiScaler backup path is not a file: {backup_path}")
            _ensure_writable(backup_path)
        dll_path.replace(backup_path)

        message = f"Backed up existing OptiScaler DLL: {dll_path.name} -> {backup_path.name}"
        if logger:
            logger.info(message)
        else:
            logging.info(message)


def remove_legacy_optiscaler_files(target_path, logger=None):
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    for file_name in sorted(OPTISCALER_LEGACY_REMOVE_NAMES):
        file_path = target_dir / file_name
        if not file_path.exists() or not file_path.is_file():
            continue

        _ensure_writable(file_path)
        try:
            file_path.unlink()
        except OSError as exc:
            message = f"Failed to remove incompatible legacy OptiScaler file: {file_path.name}"
            if logger:
                logger.error("%s (%s)", message, exc)
            else:
                logging.error("%s (%s)", message, exc)
            raise RuntimeError(message) from exc

        message = f"Removed incompatible legacy OptiScaler file: {file_path.name}"
        if logger:
            logger.info(message)
        else:
            logging.info(message)


def install_from_source_folder(source_folder, target_path, dll_name="", exclude_patterns=None, logger=None):
    if not os.path.isdir(source_folder):
        raise ValueError(f"Invalid source folder: {source_folder}")

    patterns = [str(p).strip() for p in (exclude_patterns or []) if str(p).strip()]

    for dirpath, dirnames, filenames in os.walk(source_folder):
        rel_dir = os.path.relpath(dirpath, source_folder)
        rel_dir_norm = _normalize_rel_path(rel_dir)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not _should_exclude_rel_path(
                f"{rel_dir_norm}/{dirname}" if rel_dir_norm else dirname,
                patterns,
            )
        ]
        dest_dir = target_path if rel_dir == "." else os.path.join(target_path, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)
        for fname in filenames:
            rel_file_path = f"{rel_dir_norm}/{fname}" if rel_dir_norm else fname
            if _should_exclude_rel_path(rel_file_path, patterns):
                continue
            src = os.path.join(dirpath, fname)
            dst = os.path.join(dest_dir, fname)
            shutil.copy2(src, dst)

    _rename_optiscaler_dll(target_path, dll_name, logger=logger)


def _is_archive_member_path_safe(target_dir: Path, member_name: str) -> bool:
    raw_name = str(member_name or "").replace("\\", "/").strip()
    if not raw_name:
        return True
    if raw_name.startswith("/") or raw_name.startswith("../"):
        return False
    if ".." in Path(raw_name).parts:
        return False

    try:
        resolved_target = target_dir.resolve(strict=False)
        resolved_member = (resolved_target / raw_name).resolve(strict=False)
        resolved_member.relative_to(resolved_target)
        return True
    except Exception:
        return False


def extract_archive(archive_path, target_path, logger=None):
    ext = os.path.splitext(archive_path)[1].lower()
    target_dir = Path(target_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    extractor_errors = []
    tar_exe = shutil.which("tar")

    def _extract_with_tar():
        listing = subprocess.run(
            [tar_exe, "-tf", archive_path],
            capture_output=True,
            text=True,
            check=True,
            **subprocess_no_window_kwargs(),
        )
        for member_name in listing.stdout.splitlines():
            if not _is_archive_member_path_safe(target_dir, member_name):
                raise ValueError(f"Unsafe archive entry path: {member_name}")
        subprocess.run(
            [tar_exe, "-xf", archive_path, "-C", target_path],
            check=True,
            **subprocess_no_window_kwargs(),
        )
        if logger:
            logger.info("Extracted archive %s to %s using tar.exe", archive_path, target_path)

    try:
        if ext == ".zip":
            try:
                with zipfile.ZipFile(archive_path, "r") as z:
                    for member in z.infolist():
                        if not _is_archive_member_path_safe(target_dir, member.filename):
                            raise ValueError(f"Unsafe archive entry path: {member.filename}")
                    for member in z.infolist():
                        z.extract(member, target_path)
                if logger:
                    logger.info("Extracted .zip archive %s to %s", archive_path, target_path)
                return
            except Exception as e:
                extractor_errors.append(f"zipfile: {e}")
                if logger:
                    logger.warning("Python zipfile extraction failed, trying tar fallback: %s", e)
                else:
                    logging.warning("Python zipfile extraction failed, trying tar fallback: %s", e)

            if tar_exe:
                try:
                    _extract_with_tar()
                    return
                except subprocess.CalledProcessError as e:
                    extractor_errors.append(f"tar: {e}")
                    if logger:
                        logger.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)
                    else:
                        logging.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)
            raise RuntimeError(f"Failed to extract .zip file: {archive_path}")

        if ext == ".7z":
            if tar_exe:
                # Prefer tar/libarchive first for .7z because some filter chains
                # (for example BCJ2) are supported there but not by py7zr.
                try:
                    _extract_with_tar()
                    return
                except subprocess.CalledProcessError as e:
                    extractor_errors.append(f"tar: {e}")
                    if py7zr is not None:
                        if logger:
                            logger.warning("tar.exe extraction failed (%s), trying py7zr fallback: %s", archive_path, e)
                        else:
                            logging.warning("tar.exe extraction failed (%s), trying py7zr fallback: %s", archive_path, e)
                    elif logger:
                        logger.warning("tar.exe extraction failed (%s) and no py7zr fallback is available: %s", archive_path, e)
                    else:
                        logging.warning(
                            "tar.exe extraction failed (%s) and no py7zr fallback is available: %s",
                            archive_path,
                            e,
                        )

            if py7zr is not None:
                try:
                    with py7zr.SevenZipFile(archive_path, "r") as archive:
                        for member_name in archive.getnames():
                            if not _is_archive_member_path_safe(target_dir, member_name):
                                raise ValueError(f"Unsafe archive entry path: {member_name}")
                        archive.extractall(path=target_path)
                    if logger:
                        logger.info("Extracted .7z archive %s to %s using py7zr", archive_path, target_path)
                    return
                except Exception as e:
                    extractor_errors.append(f"py7zr: {e}")
                    if tar_exe:
                        if logger:
                            logger.warning("py7zr extraction failed (%s) after tar fallback: %s", archive_path, e)
                        else:
                            logging.warning("py7zr extraction failed (%s) after tar fallback: %s", archive_path, e)
                    elif logger:
                        logger.warning("py7zr extraction failed (%s): %s", archive_path, e)
                    else:
                        logging.warning("py7zr extraction failed (%s): %s", archive_path, e)

            reasons = "; ".join(extractor_errors) if extractor_errors else "no extractor attempted"
            raise RuntimeError(
                "Cannot extract .7z archive. Install the optional 'py7zr' dependency or provide "
                f"a tar.exe build with 7z support. Details: {reasons}"
            )

        if tar_exe:
            try:
                _extract_with_tar()
                return
            except subprocess.CalledProcessError as e:
                extractor_errors.append(f"tar: {e}")
                if logger:
                    logger.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)
                else:
                    logging.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)

        raise ValueError(f"Unsupported archive format: {ext}")
    except Exception as e:
        if logger:
            logger.error("Failed to extract archive %s to %s: %s", archive_path, target_path, e)
        raise


def _rename_optiscaler_dll(target_path, dll_name, logger=None):
    if not dll_name:
        return
    src = os.path.join(target_path, OPTISCALER_DLL)
    dst = os.path.join(target_path, dll_name)
    if not os.path.exists(src):
        if logger:
            logger.warning("%s not found in %s, skipping rename.", OPTISCALER_DLL, target_path)
        else:
            logging.warning("%s not found in %s, skipping rename.", OPTISCALER_DLL, target_path)
        return
    if os.path.exists(dst):
        os.remove(dst)
    os.rename(src, dst)
    if logger:
        logger.info("Renamed %s -> %s", OPTISCALER_DLL, dll_name)
    else:
        logging.info("Renamed %s -> %s", OPTISCALER_DLL, dll_name)


def download_to_file(url, dest_path, timeout=60, logger=None):
    tmp_path = None
    try:
        response = _file_session.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        p = Path(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_name(p.name + ".tmp")
        with tmp_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        tmp_path.replace(p)
        if logger:
            logger.info("Downloaded file from %s to %s", url, dest_path)
    except Exception as e:
        try:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            logging.debug("Failed to remove temp download file: %s", tmp_path)
        if logger:
            logger.error("Failed to download %s to %s: %s", url, dest_path, e)
        raise


def _resolve_optipatcher_download_name(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    file_name = os.path.basename(parsed.path).strip()
    return file_name or OPTIPATCHER_PLUGIN_NAME


def _resolve_specialk_download_name(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    file_name = os.path.basename(parsed.path).strip()
    suffix = Path(file_name).suffix.lower()
    if file_name and (suffix in SPECIALK_ARCHIVE_EXTENSIONS or file_name.lower() == SPECIALK64_DLL_NAME.lower()):
        return file_name
    return "SpecialK.7z"


def _resolve_optipatcher_payload(download_path: Path, extract_dir: Path, logger=None) -> Path:
    if download_path.suffix.lower() not in OPTIPATCHER_ARCHIVE_EXTENSIONS:
        return download_path

    extract_archive(str(download_path), str(extract_dir), logger=logger)
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


def _resolve_specialk_payload(download_path: Path, extract_dir: Path, logger=None) -> Path:
    if download_path.suffix.lower() not in SPECIALK_ARCHIVE_EXTENSIONS:
        if download_path.name.lower() != SPECIALK64_DLL_NAME.lower():
            raise FileNotFoundError(f"Special K payload must be {SPECIALK64_DLL_NAME}: {download_path.name}")
        return download_path

    extract_archive(str(download_path), str(extract_dir), logger=logger)
    candidates = [
        candidate
        for candidate in extract_dir.rglob("*")
        if candidate.is_file() and candidate.name.lower() == SPECIALK64_DLL_NAME.lower()
    ]
    if not candidates:
        raise FileNotFoundError(f"{SPECIALK64_DLL_NAME} was not found inside the Special K archive")
    if len(candidates) > 1:
        normalized_candidates = ", ".join(
            sorted(str(candidate.relative_to(extract_dir)).replace("\\", "/") for candidate in candidates)
        )
        raise RuntimeError(f"Multiple {SPECIALK64_DLL_NAME} payload candidates were found: {normalized_candidates}")
    return candidates[0]


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
        _ensure_writable(plugin_path)
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


def install_optipatcher(target_path, url, logger=None, cached_archive_path=""):
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    plugins_dir = target_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    destination_path = plugins_dir / OPTIPATCHER_PLUGIN_NAME

    cached = Path(str(cached_archive_path or "").strip()) if cached_archive_path else None
    use_cache = cached is not None and cached.is_file()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        if use_cache:
            download_path = cached
        else:
            download_name = _resolve_optipatcher_download_name(url)
            download_path = tmpdir_path / download_name
            download_to_file(url, str(download_path), timeout=30, logger=logger)

        extract_dir = tmpdir_path / "payload"
        payload_path = _resolve_optipatcher_payload(download_path, extract_dir, logger=logger)

        _remove_existing_optipatcher_plugins(plugins_dir, logger=logger)

        if destination_path.exists():
            _ensure_writable(destination_path)
        shutil.copy2(payload_path, destination_path)

    if logger:
        logger.info("OptiPatcher installed to %s", destination_path)


def install_specialk(target_path, final_dll_name, url="", logger=None, cached_archive_path="", existing_prepared=False):
    target_dir = Path(str(target_path or "").strip())
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    normalized_final_name = Path(str(final_dll_name or "").strip()).name
    if not normalized_final_name:
        raise RuntimeError("Special K install requires the final OptiScaler DLL name.")

    plugins_dir = target_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    destination_path = plugins_dir / normalized_final_name

    cached = Path(str(cached_archive_path or "").strip()) if cached_archive_path else None
    use_cache = cached is not None and cached.is_file()
    normalized_url = str(url or "").strip()

    if not use_cache and not normalized_url:
        if existing_prepared and destination_path.is_file():
            if logger:
                logger.info(
                    "Special K cached install skipped: no download link or cache; keeping migrated file at %s",
                    destination_path,
                )
            return False
        raise FileNotFoundError("Special K download link is not configured")

    tmpdir_path = target_dir / f".optiscaler_specialk_tmp_{uuid.uuid4().hex}"
    tmpdir_path.mkdir(parents=False, exist_ok=False)
    try:
        if use_cache:
            download_path = cached
        else:
            download_name = _resolve_specialk_download_name(normalized_url)
            download_path = tmpdir_path / download_name
            download_to_file(normalized_url, str(download_path), timeout=60, logger=logger)

        extract_dir = tmpdir_path / "payload"
        payload_path = _resolve_specialk_payload(download_path, extract_dir, logger=logger)

        if destination_path.exists():
            if not destination_path.is_file():
                raise RuntimeError(f"Existing Special K plugin destination is not a file: {destination_path}")
            _ensure_writable(destination_path)
        shutil.copy2(payload_path, destination_path)
    finally:
        shutil.rmtree(tmpdir_path, ignore_errors=True)

    if logger:
        logger.info("Special K installed to %s", destination_path)
    return True


def install_unreal5_from_url(url, target_path, logger=None, cached_archive_path=""):
    cached = Path(str(cached_archive_path or "").strip()) if cached_archive_path else None
    use_cache = cached is not None and cached.is_file()

    if not use_cache:
        parsed = urlparse(url)
        file_name = os.path.basename(parsed.path)
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in {".zip", ".7z"}:
            msg = f"Unreal5 URL must point to .zip or .7z archive: {url}"
            if logger:
                logger.error(msg)
            raise ValueError(msg)

    if target_has_filename(target_path, "dxgi.dll"):
        if logger:
            logger.info("Skipped Unreal5 patch install because dxgi.dll already exists in %s", target_path)
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        if use_cache:
            archive_path = str(cached)
        else:
            archive_path = str(Path(tmpdir) / (file_name or f"unreal5_patch{ext}"))
            download_to_file(url, archive_path, timeout=60, logger=logger)
        extract_archive(archive_path, target_path, logger=logger)
        if logger:
            logger.info("Unreal5 patch installed from %s", cached_archive_path or url)
    return True


def install_reframework_dinput8_from_url(url, target_path, logger=None):
    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path) or "reframework.zip"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = str(Path(tmpdir) / file_name)
        download_to_file(url, archive_path, timeout=60, logger=logger)

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
        if logger:
            logger.info("REFramework dinput8.dll installed from URL: %s", url)
