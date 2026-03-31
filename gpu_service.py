from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import os
import subprocess
from typing import Mapping


_ALLOWED_VENDOR_KEYWORDS = (
    "intel", "amd", "nvidia",
    "arc",
    "radeon",
    "geforce", "rtx",
)

_VENDOR_KEYWORD_MAP = {
    "nvidia": ("nvidia", "geforce", "rtx"),
    "amd": ("amd", "radeon"),
    "intel": ("intel", "arc"),
}

_VENDOR_PRIORITY = ("nvidia", "amd", "intel")


@dataclass(frozen=True)
class GpuContext:
    gpu_names: list[str]
    gpu_count: int
    gpu_info: str
    vendors: list[str]
    selected_vendor: str
    selected_gid: int

    @property
    def is_multi_gpu(self) -> bool:
        return self.gpu_count > 1


def _subprocess_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    kwargs["startupinfo"] = startupinfo
    return kwargs


def get_graphics_adapter_snapshot() -> tuple[list[str], int, str]:
    """Return unique GPU names, detected adapter count, and a user-facing summary string."""
    if os.name != "nt":
        return [], 0, "Unknown (non-Windows OS)"

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8,
            **_subprocess_no_window_kwargs(),
        )
        if result.returncode != 0:
            return [], 0, "Unknown"

        gpu_names_raw = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if name:
                gpu_names_raw.append(name)

        filtered_names_unique = []
        seen_filtered_names = set()
        for name in gpu_names_raw:
            lowered = name.lower()
            if "mirage driver" in lowered:
                continue
            if any(keyword in lowered for keyword in _ALLOWED_VENDOR_KEYWORDS):
                normalized_name = " ".join(lowered.split())
                if normalized_name not in seen_filtered_names:
                    seen_filtered_names.add(normalized_name)
                    filtered_names_unique.append(name)

        if filtered_names_unique:
            # Only treat distinct GPU names as multi-GPU so duplicate WMI rows do not block installation.
            return filtered_names_unique, len(filtered_names_unique), ", ".join(filtered_names_unique)
    except Exception:
        pass

    return [], 0, "Unknown"


def get_graphics_adapter_info() -> str:
    """Return a user-friendly GPU name string for the current Windows machine."""
    return get_graphics_adapter_snapshot()[2]


def detect_gpu_vendors(gpu_info: str) -> list[str]:
    lowered = str(gpu_info or "").strip().lower()
    if not lowered:
        return []

    # Prefer discrete GPUs first when multiple adapters are present.
    return [
        vendor
        for vendor in _VENDOR_PRIORITY
        if any(keyword in lowered for keyword in _VENDOR_KEYWORD_MAP[vendor])
    ]


def resolve_game_db_target_for_gpu(
    gpu_info: str,
    vendor_db_gids: Mapping[str, int],
    default_gid: int,
) -> tuple[str, int]:
    vendors = detect_gpu_vendors(gpu_info)
    for vendor in vendors:
        gid = int(vendor_db_gids.get(vendor, default_gid) or default_gid)
        if gid:
            return vendor, gid
    return "default", int(default_gid)


def detect_gpu_context(vendor_db_gids: Mapping[str, int], default_gid: int) -> GpuContext:
    gpu_names, gpu_count, gpu_info = get_graphics_adapter_snapshot()
    vendors = detect_gpu_vendors(gpu_info)
    selected_vendor, selected_gid = resolve_game_db_target_for_gpu(gpu_info, vendor_db_gids, default_gid)
    return GpuContext(
        gpu_names=list(gpu_names),
        gpu_count=max(0, int(gpu_count or 0)),
        gpu_info=gpu_info,
        vendors=vendors,
        selected_vendor=selected_vendor,
        selected_gid=int(selected_gid or default_gid),
    )


def _split_gpu_rule_patterns(rule_text: str) -> list[str]:
    text = str(rule_text or "").strip()
    if not text:
        return []

    normalized = text.replace("\r", "\n").replace("\n", "|").replace(";", "|").replace(",", "|")
    return [token.strip().lower() for token in normalized.split("|") if token.strip()]


def matches_gpu_rule(rule_text: str, gpu_text: str) -> bool:
    patterns = _split_gpu_rule_patterns(rule_text)
    if not patterns:
        return False

    if any(pattern == "all" for pattern in patterns):
        return True

    normalized_gpu = str(gpu_text or "").strip().lower()
    if normalized_gpu in {"", "checking gpu...", "unknown"}:
        return False

    for pattern in patterns:
        if pattern in {"null", "none"}:
            continue
        if any(char in pattern for char in "*?[]"):
            if fnmatch.fnmatch(normalized_gpu, pattern):
                return True
        elif pattern in normalized_gpu:
            return True
    return False
