from __future__ import annotations

from pathlib import Path


OPTISCALER_REQUIRED_PAYLOAD_FILES = ("OptiScaler.dll", "OptiScaler.ini")


def resolve_payload_source_dir(extract_root: str | Path) -> Path:
    root_path = Path(extract_root)
    if not root_path.is_dir():
        return root_path

    contents = [child for child in root_path.iterdir()]
    if len(contents) == 1 and contents[0].is_dir():
        return contents[0]
    return root_path


def validate_optiscaler_payload_dir(payload_dir: str | Path) -> Path:
    payload_path = Path(payload_dir)
    if not payload_path.is_dir():
        raise FileNotFoundError(f"OptiScaler payload directory was not found: {payload_path}")

    if not any(payload_path.iterdir()):
        raise RuntimeError(f"OptiScaler payload directory is empty: {payload_path}")

    for required_name in OPTISCALER_REQUIRED_PAYLOAD_FILES:
        required_path = payload_path / required_name
        if not required_path.is_file():
            raise FileNotFoundError(f"Missing required OptiScaler payload file: {required_name}")

    return payload_path


__all__ = [
    "OPTISCALER_REQUIRED_PAYLOAD_FILES",
    "resolve_payload_source_dir",
    "validate_optiscaler_payload_dir",
]
