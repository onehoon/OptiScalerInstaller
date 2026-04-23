from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArchiveRuntimeState:
    optiscaler_ready: bool = False
    optiscaler_downloading: bool = False
    optiscaler_error: str = ""
    optiscaler_filename: str = ""
    opti_source_archive: str = ""
    fsr4_ready: bool = False
    fsr4_downloading: bool = False
    fsr4_error: str = ""
    fsr4_filename: str = ""
    fsr4_source_archive: str = ""
    optipatcher_ready: bool = False
    optipatcher_downloading: bool = False
    optipatcher_error: str = ""
    optipatcher_filename: str = ""
    optipatcher_source_archive: str = ""
    specialk_ready: bool = False
    specialk_downloading: bool = False
    specialk_error: str = ""
    specialk_filename: str = ""
    specialk_source_archive: str = ""
    ual_ready: bool = False
    ual_downloading: bool = False
    ual_error: str = ""
    ual_filename: str = ""
    ual_source_archive: str = ""
    unreal5_ready: bool = False
    unreal5_downloading: bool = False
    unreal5_error: str = ""
    unreal5_filename: str = ""
    unreal5_source_archive: str = ""


@dataclass
class GpuRuntimeState:
    gpu_names: list[str] = field(default_factory=list)
    gpu_count: int = 0
    is_multi_gpu: bool = False
    multi_gpu_blocked: bool = False
    gpu_selection_pending: bool = False
    gpu_info: str = ""
    gpu_context: Any | None = None
    selected_adapter: Any | None = None


@dataclass
class SheetRuntimeState:
    status: bool = False
    loading: bool = True
    active_vendor: str = "default"
    game_db: dict[str, Any] = field(default_factory=dict)
    module_download_links: dict[str, Any] = field(default_factory=dict)


@dataclass
class InstallRuntimeState:
    in_progress: bool = False
    popup_confirmed: bool = False
    precheck_running: bool = False
    precheck_ok: bool = False
    precheck_error: str = ""
    precheck_dll_name: str = ""
    precheck_ual_detected_names: tuple[str, ...] = ()
    rtss_scan_ok: bool = False
    rtss_installed: bool = False
    rtss_profiles_global_exists: bool = False
    rtss_global_fix_needed: bool = False


@dataclass
class CardUiRuntimeState:
    selected_game_index: int | None = None
    hovered_card_index: int | None = None
    image_updates_suspended: bool = False
    deferred_image_update_indices: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class RuntimeStateBundle:
    archive_state: ArchiveRuntimeState
    gpu_state: GpuRuntimeState
    sheet_state: SheetRuntimeState
    install_state: InstallRuntimeState
    card_ui_state: CardUiRuntimeState


_RUNTIME_STATE_OBJECT_MAP = {
    "archive_state": ("_archive_state", ArchiveRuntimeState),
    "gpu_state": ("_gpu_state", GpuRuntimeState),
    "sheet_state": ("_sheet_state", SheetRuntimeState),
    "install_state": ("_install_state", InstallRuntimeState),
    "card_ui_state": ("_card_ui_state", CardUiRuntimeState),
}


def build_runtime_state_bundle(*, checking_gpu_text: str) -> RuntimeStateBundle:
    return RuntimeStateBundle(
        archive_state=ArchiveRuntimeState(),
        gpu_state=GpuRuntimeState(gpu_info=str(checking_gpu_text or "")),
        sheet_state=SheetRuntimeState(),
        install_state=InstallRuntimeState(),
        card_ui_state=CardUiRuntimeState(),
    )


def get_runtime_state_attr(instance: Any, name: str) -> Any:
    state_object = _RUNTIME_STATE_OBJECT_MAP.get(name)
    if state_object is not None:
        storage_name, factory = state_object
        state = instance.__dict__.get(storage_name)
        if state is None:
            state = factory()
            object.__setattr__(instance, storage_name, state)
        return state

    raise AttributeError(f"{type(instance).__name__!s} object has no attribute {name!r}")


def set_runtime_state_attr(instance: Any, name: str, value: Any) -> bool:
    state_object = _RUNTIME_STATE_OBJECT_MAP.get(name)
    if state_object is not None:
        storage_name, _factory = state_object
        object.__setattr__(instance, storage_name, value)
        return True

    return False


__all__ = [
    "ArchiveRuntimeState",
    "CardUiRuntimeState",
    "GpuRuntimeState",
    "InstallRuntimeState",
    "RuntimeStateBundle",
    "SheetRuntimeState",
    "build_runtime_state_bundle",
    "get_runtime_state_attr",
    "set_runtime_state_attr",
]
