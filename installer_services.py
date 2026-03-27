import fnmatch
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
import ctypes
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse

from network_utils import build_retry_session

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
# Legacy OptiScaler-side compatibility cleanup targets.
# These names are intentionally removed as stale OptiScaler artifacts, not treated as third-party mods.
OPTISCALER_LEGACY_REMOVE_NAMES = {
    "nvapi64.dll",
    "nvngx.dll",
    "OptiScaler.asi",
}
OPTISCALER_PROXY_FALLBACK_NAMES = ("winmm.dll", "version.dll")

_file_session = build_retry_session()


def _subprocess_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs


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


def _next_backup_path(file_path: Path) -> Path:
    candidate = file_path.with_name(file_path.name + ".bak")
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        candidate = file_path.with_name(f"{file_path.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _ensure_writable(file_path: Path) -> None:
    try:
        os.chmod(file_path, 0o666)
    except OSError:
        logging.debug("Failed to update file attributes for %s", file_path)


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


def resolve_proxy_dll_name(target_path, preferred_name="", logger=None) -> str:
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

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
                logger.info("Selected proxy DLL name for OptiScaler: %s", candidate)
            return candidate
        if _is_optiscaler_managed_proxy_dll(candidate_path):
            if logger:
                logger.info(
                    "Selected proxy DLL name for OptiScaler after planned backup of existing OptiScaler DLL: %s",
                    candidate,
                )
            return candidate
        if logger:
            logger.info("Proxy DLL name already in use by non-OptiScaler file, skipping: %s", candidate)

    raise RuntimeError(
        "No available OptiScaler DLL names for installation. "
        f"Checked: {', '.join(candidates)}"
    )


def backup_existing_optiscaler_proxy_dlls(target_path, logger=None):
    target_dir = Path(target_path)
    if not target_dir.is_dir():
        raise ValueError(f"Invalid target folder: {target_path}")

    for dll_name in sorted(OPTISCALER_PROXY_DLL_NAMES):
        dll_path = target_dir / dll_name
        if not dll_path.exists() or not dll_path.is_file():
            continue
        if not _is_optiscaler_managed_proxy_dll(dll_path):
            message = f"Skipped backup for non-OptiScaler proxy DLL: {dll_path.name}"
            if logger:
                logger.info(message)
            else:
                logging.info(message)
            continue

        backup_path = _next_backup_path(dll_path)
        _ensure_writable(dll_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dll_path), str(backup_path))

        message = f"Backed up existing proxy DLL: {dll_path.name} -> {backup_path.name}"
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
                    logger.info(f"Extracted .zip archive {archive_path} to {target_path}")
                return
            except Exception as e:
                extractor_errors.append(f"zipfile: {e}")
                if logger:
                    logger.warning(f"Python zipfile extraction failed, trying tar fallback: {e}")
                else:
                    logging.warning("Python zipfile extraction failed, trying tar fallback: %s", e)

        if ext == ".7z" and py7zr is not None:
            try:
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    for member_name in archive.getnames():
                        if not _is_archive_member_path_safe(target_dir, member_name):
                            raise ValueError(f"Unsafe archive entry path: {member_name}")
                    archive.extractall(path=target_path)
                if logger:
                    logger.info(f"Extracted .7z archive {archive_path} to {target_path} using py7zr")
                return
            except Exception as e:
                extractor_errors.append(f"py7zr: {e}")
                if logger:
                    logger.warning(f"py7zr extraction failed ({archive_path}), trying tar fallback: {e}")
                else:
                    logging.warning("py7zr extraction failed (%s), trying tar fallback: %s", archive_path, e)

        tar_exe = shutil.which("tar")
        if tar_exe:
            try:
                listing = subprocess.run(
                    [tar_exe, "-tf", archive_path],
                    capture_output=True,
                    text=True,
                    check=True,
                    **_subprocess_no_window_kwargs(),
                )
                for member_name in listing.stdout.splitlines():
                    if not _is_archive_member_path_safe(target_dir, member_name):
                        raise ValueError(f"Unsafe archive entry path: {member_name}")
                subprocess.run(
                    [tar_exe, "-xf", archive_path, "-C", target_path],
                    check=True,
                    **_subprocess_no_window_kwargs(),
                )
                if logger:
                    logger.info(f"Extracted archive {archive_path} to {target_path} using tar.exe")
                return
            except subprocess.CalledProcessError as e:
                extractor_errors.append(f"tar: {e}")
                if logger:
                    logger.warning(f"tar.exe extraction failed ({archive_path}), falling back: {e}")
                else:
                    logging.warning("tar.exe extraction failed (%s), falling back: %s", archive_path, e)

        if ext == ".zip":
            raise RuntimeError(f"Failed to extract .zip file: {archive_path}")
        if ext == ".7z":
            reasons = "; ".join(extractor_errors) if extractor_errors else "no extractor attempted"
            raise RuntimeError(
                "Cannot extract .7z archive. Install the optional 'py7zr' dependency or provide "
                f"a tar.exe build with 7z support. Details: {reasons}"
            )
        raise ValueError(f"Unsupported archive format: {ext}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to extract archive {archive_path} to {target_path}: {e}")
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
            logger.info(f"Downloaded file from {url} to {dest_path}")
    except Exception as e:
        try:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            logging.debug("Failed to remove temp download file: %s", tmp_path)
        if logger:
            logger.error(f"Failed to download {url} to {dest_path}: {e}")
        raise


def install_optipatcher(target_path, url, logger=None):
    plugins_dir = os.path.join(target_path, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)

    asi_path = os.path.join(plugins_dir, "OptiPatcher.asi")
    download_to_file(url, asi_path, timeout=30, logger=logger)
    if logger:
        logger.info(f"OptiPatcher downloaded to {asi_path}")


def install_unreal5_from_url(url, target_path, logger=None):
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
        archive_path = str(Path(tmpdir) / (file_name or f"unreal5_patch{ext}"))
        download_to_file(url, archive_path, timeout=60, logger=logger)
        extract_archive(archive_path, target_path, logger=logger)
        if logger:
            logger.info(f"Unreal5 patch installed from URL: {url}")
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
            logger.info(f"REFramework dinput8.dll installed from URL: {url}")
