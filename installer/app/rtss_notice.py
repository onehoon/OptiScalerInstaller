from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

if os.name == "nt":
    import winreg


def _log_info_if_logger(logger: Any, message: str, *args) -> None:
    if logger:
        logger.info(message, *args)
    else:
        logging.info(message, *args)


def _log_warning(logger: Any, message: str, *args) -> None:
    if logger:
        logger.warning(message, *args)
    else:
        logging.warning(message, *args)


_RTSS_GAME_PROFILE_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "OSD": (
        ("EnableOSD", "0"),
    ),
    "Hooking": (
        ("EnableHooking", "0"),
        ("HookDirect3D8", "0"),
        ("HookDirect3D9", "0"),
        ("HookDXGI", "0"),
        ("HookDirect3D12", "0"),
        ("HookOpenGL", "0"),
        ("HookVulkan", "0"),
        ("UseDetours", "1"),
    ),
    "Framerate": (
        ("ReflexSetLatencyMarker", "0"),
    ),
}


def _get_rtss_install_path() -> Path:
    if os.name == "nt":
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        subkeys = [r"SOFTWARE\WOW6432Node\Unwinder\RTSS", r"SOFTWARE\Unwinder\RTSS"]

        for root in roots:
            for subkey in subkeys:
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                        val, _ = winreg.QueryValueEx(key, "InstallPath")
                        if val:
                            path = Path(val)
                            if path.is_file() and path.name.lower() == "rtss.exe":
                                path = path.parent
                            if path.exists():
                                return path
                except Exception:
                    continue

    return Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "RivaTuner Statistics Server"


def _read_rtss_global_settings(global_path: Path) -> tuple[Optional[str], Optional[str]]:
    ref_val, detours_val = None, None
    lines = global_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    for line in lines:
        line = line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key == "ReflexSetLatencyMarker":
            ref_val = normalized_value
        elif normalized_key == "UseDetours":
            detours_val = normalized_value
    return ref_val, detours_val


def _is_rtss_config_ok(ref_val: Optional[str], detours_val: Optional[str]) -> bool:
    return ref_val == "0" and detours_val == "1"


def _write_rtss_global_settings(global_path: Path, logger: Any = None) -> None:
    target_keys = {
        "ReflexSetLatencyMarker": "0",
        "UseDetours": "1",
    }

    raw_bytes = global_path.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    content_bytes = raw_bytes[3:] if has_bom else raw_bytes

    if b"\r\n" in content_bytes:
        line_ending = "\r\n"
    elif b"\r" in content_bytes:
        line_ending = "\r"
    else:
        line_ending = "\n"

    text = content_bytes.decode("utf-8", errors="ignore")
    lines = text.splitlines()

    new_lines = []
    applied_keys: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in target_keys:
                new_lines.append(f"{key}={target_keys[key]}")
                applied_keys.add(key)
                continue
        new_lines.append(line)

    for key, value in target_keys.items():
        if key not in applied_keys:
            new_lines.append(f"{key}={value}")
            _log_info_if_logger(logger, "[RTSS] Key not found in Global file, appending: %s=%s", key, value)

    new_text = line_ending.join(new_lines)
    if text.endswith("\n") or text.endswith("\r"):
        new_text += line_ending

    encoded = new_text.encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded

    global_path.write_bytes(encoded)
    _log_info_if_logger(logger, "[RTSS] Global file updated: %s", global_path)


def _decode_rtss_text(raw_bytes: bytes) -> tuple[str, str, bytes]:
    # Preserve BOM and original encoding family whenever possible.
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return raw_bytes[3:].decode("utf-8", errors="ignore"), "utf-8", b"\xef\xbb\xbf"
    if raw_bytes.startswith(b"\xff\xfe"):
        return raw_bytes[2:].decode("utf-16-le", errors="ignore"), "utf-16-le", b"\xff\xfe"
    if raw_bytes.startswith(b"\xfe\xff"):
        return raw_bytes[2:].decode("utf-16-be", errors="ignore"), "utf-16-be", b"\xfe\xff"
    try:
        return raw_bytes.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1", errors="ignore"), "latin-1", b""


def _encode_rtss_text(text: str, *, encoding: str, bom: bytes) -> bytes:
    return bom + text.encode(encoding, errors="ignore")


def _detect_line_ending(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def _apply_rtss_ini_key_values(text: str, section_key_pairs: dict[str, tuple[tuple[str, str], ...]]) -> str:
    line_ending = _detect_line_ending(text)
    had_trailing_newline = text.endswith(("\r", "\n"))
    lines = text.splitlines()

    normalized_targets: dict[str, dict[str, str]] = {
        section.casefold(): {key.casefold(): value for key, value in key_values}
        for section, key_values in section_key_pairs.items()
    }
    section_name_by_norm: dict[str, str] = {
        section.casefold(): section
        for section in section_key_pairs
    }

    applied: dict[str, set[str]] = {
        section.casefold(): set()
        for section in section_key_pairs
    }
    found_sections: set[str] = set()

    current_section_norm = ""
    output_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) >= 3:
            current_section_norm = stripped[1:-1].strip().casefold()
            if current_section_norm in normalized_targets:
                found_sections.add(current_section_norm)
            output_lines.append(line)
            continue

        if not stripped or stripped.startswith(";") or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(line)
            continue

        key_raw, _ = stripped.split("=", 1)
        key_norm = key_raw.strip().casefold()
        section_targets = normalized_targets.get(current_section_norm)
        if section_targets and key_norm in section_targets:
            output_lines.append(f"{key_raw.strip()}={section_targets[key_norm]}")
            applied[current_section_norm].add(key_norm)
            continue

        output_lines.append(line)

    # Append missing keys in existing sections first, then append missing sections.
    for section_norm, key_values in normalized_targets.items():
        if section_norm in found_sections:
            for key, value in section_key_pairs[section_name_by_norm[section_norm]]:
                key_norm = key.casefold()
                if key_norm not in applied[section_norm]:
                    output_lines.append(f"{key}={value}")

    for section, key_values in section_key_pairs.items():
        section_norm = section.casefold()
        if section_norm in found_sections:
            continue
        output_lines.append(f"[{section}]")
        for key, value in key_values:
            output_lines.append(f"{key}={value}")

    new_text = line_ending.join(output_lines)
    if had_trailing_newline:
        new_text += line_ending
    return new_text


def _write_rtss_game_profile_settings(profile_path: Path, logger: Any = None) -> None:
    raw_bytes = profile_path.read_bytes()
    text, encoding, bom = _decode_rtss_text(raw_bytes)
    updated_text = _apply_rtss_ini_key_values(text, _RTSS_GAME_PROFILE_KEYS)
    profile_path.write_bytes(_encode_rtss_text(updated_text, encoding=encoding, bom=bom))
    _log_info_if_logger(logger, "[RTSS] Game profile updated: %s", profile_path)


def _restart_rtss_if_running_silent(logger: Any = None) -> None:
    if os.name != "nt":
        return

    install_path = _get_rtss_install_path()
    rtss_exe_path = install_path / "RTSS.exe"
    if not rtss_exe_path.exists():
        _log_info_if_logger(logger, "[RTSS] RTSS executable not found, skipping restart: %s", rtss_exe_path)
        return

    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    detached_process = int(getattr(subprocess, "DETACHED_PROCESS", 0))
    creation_flags = create_no_window | detached_process

    try:
        probe = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq RTSS.exe"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=create_no_window,
        )
        if "RTSS.exe" not in str(probe.stdout or ""):
            _log_info_if_logger(logger, "[RTSS] RTSS process not running; skip restart")
            return

        subprocess.run(
            ["taskkill", "/IM", "RTSS.exe", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
            creationflags=create_no_window,
        )
        subprocess.Popen(
            [str(rtss_exe_path)],
            cwd=str(install_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        _log_info_if_logger(logger, "[RTSS] Restarted RTSS silently")
    except Exception as exc:
        _log_warning(logger, "[RTSS] Failed to restart RTSS silently: %s", exc)


def apply_rtss_game_profile_overlay_if_needed(game_data: dict[str, Any], logger: Any = None) -> None:
    """When rtss_overlay=true, copy Profiles/Global -> Profiles/<game.exe>.cfg and patch keys."""
    try:
        if not bool((game_data or {}).get("rtss_overlay")):
            return

        exe_name = Path(str((game_data or {}).get("exe") or "")).name.strip()
        if not exe_name:
            exe_name = Path(str((game_data or {}).get("exe_path") or (game_data or {}).get("match_anchor") or "")).name.strip()
        if not exe_name:
            _log_info_if_logger(logger, "[RTSS] Missing game exe name; skipping game profile overlay")
            return

        install_path = _get_rtss_install_path()
        profiles_dir = install_path / "Profiles"
        global_path = profiles_dir / "Global"
        if not global_path.exists():
            _log_info_if_logger(logger, "[RTSS] Global file not found, skipping game profile overlay: %s", global_path)
            return

        profile_path = profiles_dir / f"{exe_name}.cfg"

        import stat as _stat
        orig_mode = None
        orig_readonly = False
        if profile_path.exists():
            orig_mode = profile_path.stat().st_mode
            orig_readonly = not (orig_mode & _stat.S_IWRITE)
            if orig_readonly:
                profile_path.chmod(orig_mode | _stat.S_IWRITE)

        try:
            if not profile_path.exists():
                profile_path.write_bytes(global_path.read_bytes())
                _log_info_if_logger(logger, "[RTSS] Created game profile from Global: %s", profile_path)
            else:
                _log_info_if_logger(logger, "[RTSS] Existing game profile found; editing in place: %s", profile_path)
            _write_rtss_game_profile_settings(profile_path, logger=logger)
            _restart_rtss_if_running_silent(logger=logger)
        finally:
            if profile_path.exists() and orig_readonly and orig_mode is not None:
                profile_path.chmod(orig_mode & ~_stat.S_IWRITE)
    except Exception as exc:
        _log_warning(logger, "[RTSS] Failed to apply game profile overlay: %s", exc)


def apply_rtss_global_settings_if_needed(logger: Any = None) -> None:
    """Called after a successful install. Silently fixes RTSS Global settings if needed.
    No popup is shown. Read-only state of the file is preserved."""
    try:
        install_path = _get_rtss_install_path()
        global_path = install_path / "Profiles" / "Global"

        if not global_path.exists():
            _log_info_if_logger(logger, "[RTSS] Global file not found, skipping fix: %s", global_path)
            return

        ref_val, detours_val = _read_rtss_global_settings(global_path)
        _log_info_if_logger(
            logger,
            "[RTSS] Pre-fix values: ReflexSetLatencyMarker=%s, UseDetours=%s",
            ref_val,
            detours_val,
        )

        if _is_rtss_config_ok(ref_val, detours_val):
            _log_info_if_logger(logger, "[RTSS] Settings already OK, no changes needed")
            return

        import stat as _stat
        orig_stat = global_path.stat()
        orig_readonly = not (orig_stat.st_mode & _stat.S_IWRITE)

        try:
            if orig_readonly:
                global_path.chmod(orig_stat.st_mode | _stat.S_IWRITE)
                _log_info_if_logger(logger, "[RTSS] Temporarily removed read-only from Global file")

            _write_rtss_global_settings(global_path, logger=logger)
        finally:
            if orig_readonly:
                global_path.chmod(orig_stat.st_mode & ~_stat.S_IWRITE)
                _log_info_if_logger(logger, "[RTSS] Restored read-only on Global file")

    except Exception as exc:
        _log_warning(logger, "[RTSS] Failed to apply Global settings fix: %s", exc)
