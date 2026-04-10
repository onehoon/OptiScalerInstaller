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
    active_gid: int = 0
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
    precheck_reshade_install_mode: str = "disabled"
    precheck_reshade_source_dll_name: str = ""
    precheck_specialk_install_mode: str = "disabled"
    precheck_specialk_source_dll_name: str = ""


@dataclass
class CardUiRuntimeState:
    selected_game_index: int | None = None
    hovered_card_index: int | None = None


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
_RUNTIME_STATE_FIELD_MAP = {
    "opti_source_archive": ("_archive_state", "opti_source_archive", ArchiveRuntimeState),
    "fsr4_source_archive": ("_archive_state", "fsr4_source_archive", ArchiveRuntimeState),
    "optiscaler_archive_ready": ("_archive_state", "optiscaler_ready", ArchiveRuntimeState),
    "optiscaler_archive_downloading": ("_archive_state", "optiscaler_downloading", ArchiveRuntimeState),
    "optiscaler_archive_error": ("_archive_state", "optiscaler_error", ArchiveRuntimeState),
    "optiscaler_archive_filename": ("_archive_state", "optiscaler_filename", ArchiveRuntimeState),
    "fsr4_archive_ready": ("_archive_state", "fsr4_ready", ArchiveRuntimeState),
    "fsr4_archive_downloading": ("_archive_state", "fsr4_downloading", ArchiveRuntimeState),
    "fsr4_archive_error": ("_archive_state", "fsr4_error", ArchiveRuntimeState),
    "fsr4_archive_filename": ("_archive_state", "fsr4_filename", ArchiveRuntimeState),
    "optipatcher_source_archive": ("_archive_state", "optipatcher_source_archive", ArchiveRuntimeState),
    "optipatcher_archive_ready": ("_archive_state", "optipatcher_ready", ArchiveRuntimeState),
    "optipatcher_archive_downloading": ("_archive_state", "optipatcher_downloading", ArchiveRuntimeState),
    "optipatcher_archive_error": ("_archive_state", "optipatcher_error", ArchiveRuntimeState),
    "optipatcher_archive_filename": ("_archive_state", "optipatcher_filename", ArchiveRuntimeState),
    "specialk_source_archive": ("_archive_state", "specialk_source_archive", ArchiveRuntimeState),
    "specialk_archive_ready": ("_archive_state", "specialk_ready", ArchiveRuntimeState),
    "specialk_archive_downloading": ("_archive_state", "specialk_downloading", ArchiveRuntimeState),
    "specialk_archive_error": ("_archive_state", "specialk_error", ArchiveRuntimeState),
    "specialk_archive_filename": ("_archive_state", "specialk_filename", ArchiveRuntimeState),
    "ual_source_archive": ("_archive_state", "ual_source_archive", ArchiveRuntimeState),
    "ual_archive_ready": ("_archive_state", "ual_ready", ArchiveRuntimeState),
    "ual_archive_downloading": ("_archive_state", "ual_downloading", ArchiveRuntimeState),
    "ual_archive_error": ("_archive_state", "ual_error", ArchiveRuntimeState),
    "ual_archive_filename": ("_archive_state", "ual_filename", ArchiveRuntimeState),
    "unreal5_source_archive": ("_archive_state", "unreal5_source_archive", ArchiveRuntimeState),
    "unreal5_archive_ready": ("_archive_state", "unreal5_ready", ArchiveRuntimeState),
    "unreal5_archive_downloading": ("_archive_state", "unreal5_downloading", ArchiveRuntimeState),
    "unreal5_archive_error": ("_archive_state", "unreal5_error", ArchiveRuntimeState),
    "unreal5_archive_filename": ("_archive_state", "unreal5_filename", ArchiveRuntimeState),
    "gpu_names": ("_gpu_state", "gpu_names", GpuRuntimeState),
    "gpu_count": ("_gpu_state", "gpu_count", GpuRuntimeState),
    "is_multi_gpu": ("_gpu_state", "is_multi_gpu", GpuRuntimeState),
    "multi_gpu_blocked": ("_gpu_state", "multi_gpu_blocked", GpuRuntimeState),
    "_gpu_selection_pending": ("_gpu_state", "gpu_selection_pending", GpuRuntimeState),
    "gpu_info": ("_gpu_state", "gpu_info", GpuRuntimeState),
    "_gpu_context": ("_gpu_state", "gpu_context", GpuRuntimeState),
    "_selected_gpu_adapter": ("_gpu_state", "selected_adapter", GpuRuntimeState),
    "sheet_status": ("_sheet_state", "status", SheetRuntimeState),
    "sheet_loading": ("_sheet_state", "loading", SheetRuntimeState),
    "active_game_db_vendor": ("_sheet_state", "active_vendor", SheetRuntimeState),
    "active_game_db_gid": ("_sheet_state", "active_gid", SheetRuntimeState),
    "game_db": ("_sheet_state", "game_db", SheetRuntimeState),
    "module_download_links": ("_sheet_state", "module_download_links", SheetRuntimeState),
    "install_in_progress": ("_install_state", "in_progress", InstallRuntimeState),
    "_game_popup_confirmed": ("_install_state", "popup_confirmed", InstallRuntimeState),
    "install_precheck_running": ("_install_state", "precheck_running", InstallRuntimeState),
    "install_precheck_ok": ("_install_state", "precheck_ok", InstallRuntimeState),
    "install_precheck_error": ("_install_state", "precheck_error", InstallRuntimeState),
    "install_precheck_dll_name": ("_install_state", "precheck_dll_name", InstallRuntimeState),
    "selected_game_index": ("_card_ui_state", "selected_game_index", CardUiRuntimeState),
    "_hovered_card_index": ("_card_ui_state", "hovered_card_index", CardUiRuntimeState),
}


def build_runtime_state_bundle(*, checking_gpu_text: str, default_sheet_gid: int) -> RuntimeStateBundle:
    return RuntimeStateBundle(
        archive_state=ArchiveRuntimeState(),
        gpu_state=GpuRuntimeState(gpu_info=str(checking_gpu_text or "")),
        sheet_state=SheetRuntimeState(active_gid=int(default_sheet_gid)),
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

    state_field = _RUNTIME_STATE_FIELD_MAP.get(name)
    if state_field is not None:
        storage_name, field_name, factory = state_field
        state = instance.__dict__.get(storage_name)
        if state is None:
            state = factory()
            object.__setattr__(instance, storage_name, state)
        return getattr(state, field_name)

    raise AttributeError(f"{type(instance).__name__!s} object has no attribute {name!r}")


def set_runtime_state_attr(instance: Any, name: str, value: Any) -> bool:
    state_object = _RUNTIME_STATE_OBJECT_MAP.get(name)
    if state_object is not None:
        storage_name, _factory = state_object
        object.__setattr__(instance, storage_name, value)
        return True

    state_field = _RUNTIME_STATE_FIELD_MAP.get(name)
    if state_field is not None:
        storage_name, field_name, factory = state_field
        state = instance.__dict__.get(storage_name)
        if state is None:
            state = factory()
            object.__setattr__(instance, storage_name, state)
        setattr(state, field_name, value)
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
