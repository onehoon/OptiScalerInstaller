from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..config import ini_utils
from ..app import rtss_notice
from . import services as installer_services
from .components import (
    OPTISCALER_ASI_NAME,
    install_optipatcher,
    install_reframework_dinput8,
    install_specialk,
    install_ultimate_asi_loader,
    install_unreal5_patch,
)


@dataclass(frozen=True)
class InstallContext:
    handler: Any
    game_data: dict[str, Any]
    source_archive: str
    target_path: str
    use_ultimate_asi_loader: bool
    final_dll_name: str
    fsr4_source_archive: str
    fsr4_required: bool
    ual_detected_names: tuple[str, ...] = ()
    reshade_install_mode: str = "disabled"
    reshade_source_dll_name: str = ""
    specialk_install_mode: str = "disabled"
    specialk_source_dll_name: str = ""


@dataclass(frozen=True)
class InstallWorkflowCallbacks:
    install_base_payload: Callable[[str, str, str, list[str], Any], None]
    apply_optional_ingame_ini_settings: Callable[[str, dict[str, Any], Any], None]
    apply_optional_engine_ini_settings: Callable[[str, dict[str, Any], Any], None]
    install_fsr4_dll: Callable[[str, str, Any], Any]


def resolve_install_exclude_patterns(module_download_links: Mapping[str, object]) -> list[str]:
    exclude_raw = str(module_download_links.get("__exclude_list__", "")).strip()
    return [token.strip() for token in exclude_raw.split("|") if token.strip()]


def build_install_context(
    app: Any,
    game_data: Mapping[str, Any],
    source_archive: str,
    resolved_dll_name: str,
    fsr4_source_archive: str,
    fsr4_required: bool,
    ual_detected_names: tuple[str, ...] | None = None,
    logger=None,
    *,
    reshade_install_mode: str = "disabled",
    reshade_source_dll_name: str = "",
    specialk_install_mode: str = "disabled",
    specialk_source_dll_name: str = "",
) -> InstallContext:
    if logger is None:
        import logging
        logger = logging.getLogger()
    from ..games.handlers import get_game_handler

    handler = get_game_handler(game_data)
    logger.info("Using game handler: %s", getattr(handler, "handler_key", "default"))

    install_plan = handler.prepare_install_plan(app, game_data, source_archive, resolved_dll_name, logger)
    planned_game_data = dict(install_plan.game_data)
    planned_source_archive = str(install_plan.source_archive or source_archive)
    planned_resolved_dll_name = str(install_plan.resolved_dll_name or resolved_dll_name)
    target_path = planned_game_data["path"]
    ual_names = tuple(ual_detected_names or ())
    ual_auto_detected = bool(ual_names)
    normalized_reshade_mode = str(reshade_install_mode or "").strip().lower() or "disabled"
    normalized_reshade_source = str(reshade_source_dll_name or "").strip()
    normalized_specialk_mode = str(specialk_install_mode or "").strip().lower() or "disabled"
    normalized_specialk_source = str(specialk_source_dll_name or "").strip()
    use_ultimate_asi_loader = bool(planned_game_data.get("ultimate_asi_loader")) or ual_auto_detected

    if use_ultimate_asi_loader and planned_game_data.get("reframework_url"):
        raise RuntimeError(
            "Ultimate ASI Loader and REFramework both require dinput8.dll, and this combination is not supported yet."
        )

    if use_ultimate_asi_loader:
        if ual_auto_detected:
            # Auto-detect mode: OptiScaler must always install as OptiScaler.asi
            final_dll_name = OPTISCALER_ASI_NAME
            logger.info("Install mode: Ultimate ASI Loader (auto-detected, forced to %s)", final_dll_name)
        else:
            final_dll_name = planned_resolved_dll_name or OPTISCALER_ASI_NAME
            logger.info("Install mode: Ultimate ASI Loader (%s)", final_dll_name)
    else:
        reusable_filenames_list = []
        if (
            installer_services.RESHADE_COMPAT_INSTALL_ENABLED
            and normalized_reshade_mode == "migrate"
            and normalized_reshade_source
        ):
            reusable_filenames_list.append(normalized_reshade_source)
        if (
            installer_services.SPECIALK_AUTO_DETECT_INSTALL_ENABLED
            and normalized_specialk_mode == "migrate"
            and normalized_specialk_source
        ):
            reusable_filenames_list.append(normalized_specialk_source)
        final_dll_name = installer_services.resolve_proxy_dll_name(
            target_path,
            planned_resolved_dll_name or str(planned_game_data.get("dll_name", "")).strip(),
            logger=logger,
            reusable_filenames=tuple(reusable_filenames_list),
        )

    return InstallContext(
        handler=handler,
        game_data=planned_game_data,
        source_archive=planned_source_archive,
        target_path=target_path,
        use_ultimate_asi_loader=use_ultimate_asi_loader,
        final_dll_name=final_dll_name,
        fsr4_source_archive=str(fsr4_source_archive or ""),
        fsr4_required=bool(fsr4_required),
        ual_detected_names=ual_names,
        reshade_install_mode=normalized_reshade_mode,
        reshade_source_dll_name=normalized_reshade_source,
        specialk_install_mode=normalized_specialk_mode,
        specialk_source_dll_name=normalized_specialk_source,
    )


def run_install_workflow(
    app: Any,
    install_ctx: InstallContext,
    module_download_links: Mapping[str, object],
    optipatcher_url: str,
    gpu_info: Any,
    callbacks: InstallWorkflowCallbacks,
    logger,
    *,
    ual_cached_archive: str = "",
    optipatcher_cached_archive: str = "",
    specialk_cached_archive: str = "",
    unreal5_cached_archive: str = "",
) -> dict[str, Any]:
    logger.info("Install started: target=%s", install_ctx.target_path)
    exclude_patterns = resolve_install_exclude_patterns(module_download_links)
    specialk_requested = bool(install_ctx.game_data.get("specialk")) or (
        installer_services.SPECIALK_AUTO_DETECT_INSTALL_ENABLED
        and install_ctx.specialk_install_mode != "disabled"
    )
    specialk_skipped_for_asi = (
        specialk_requested
        and install_ctx.final_dll_name.lower() == OPTISCALER_ASI_NAME.lower()
    )
    specialk_existing_prepared = False
    reshade_ready = installer_services.prepare_reshade_for_optiscaler(
        install_ctx.target_path,
        install_ctx.reshade_install_mode,
        install_ctx.reshade_source_dll_name,
        logger=logger,
    )
    if specialk_skipped_for_asi:
        logger.info(
            "Special K install skipped: OptiScaler.asi install mode does not support plugins/%s loading",
            install_ctx.final_dll_name,
        )
    elif (
        installer_services.SPECIALK_AUTO_DETECT_INSTALL_ENABLED
        and install_ctx.specialk_install_mode != "disabled"
    ):
        specialk_existing_prepared = installer_services.prepare_specialk_for_optiscaler(
            install_ctx.target_path,
            install_ctx.final_dll_name,
            install_ctx.specialk_install_mode,
            install_ctx.specialk_source_dll_name,
            logger=logger,
        )
    callbacks.install_base_payload(
        install_ctx.source_archive,
        install_ctx.target_path,
        install_ctx.final_dll_name,
        exclude_patterns,
        logger,
    )

    ini_path = os.path.join(install_ctx.target_path, "OptiScaler.ini")
    if not os.path.exists(ini_path):
        raise FileNotFoundError("OptiScaler.ini not found after installation")

    if install_ctx.use_ultimate_asi_loader:
        ual_names = install_ctx.ual_detected_names if install_ctx.ual_detected_names else None
        install_ultimate_asi_loader(
            install_ctx.target_path,
            module_download_links,
            ual_detected_names=ual_names,
            logger=logger,
            cached_archive_path=ual_cached_archive,
        )

    if specialk_requested and not specialk_skipped_for_asi:
        install_specialk(
            install_ctx.target_path,
            install_ctx.final_dll_name,
            module_download_links,
            logger=logger,
            cached_archive_path=specialk_cached_archive,
            existing_prepared=specialk_existing_prepared,
        )

    merged_ini_settings = dict(install_ctx.game_data.get("ini_settings", {}))
    install_reframework_dinput8(install_ctx.target_path, install_ctx.game_data, logger=logger)
    merged_ini_settings.update(
        install_optipatcher(
            install_ctx.target_path,
            install_ctx.game_data,
            module_download_links,
            str(optipatcher_url or ""),
            logger=logger,
            cached_archive_path=optipatcher_cached_archive,
        )
    )
    if reshade_ready:
        merged_ini_settings["LoadReshade"] = "true"

    ini_utils.apply_ini_settings(ini_path, merged_ini_settings, force_frame_generation=True, logger=logger)
    if specialk_requested and not specialk_skipped_for_asi:
        ini_utils.apply_ini_settings(
            ini_path,
            {"Plugins:LoadAsiPlugins": "true"},
            force_frame_generation=True,
            logger=logger,
        )
    if reshade_ready:
        ini_utils._upsert_ini_entries(Path(ini_path), {"": {"LoadReshade": "true"}}, logger=logger)
    logger.info("Applied ini settings to %s", ini_path)

    callbacks.apply_optional_ingame_ini_settings(install_ctx.target_path, install_ctx.game_data, logger)
    callbacks.apply_optional_engine_ini_settings(install_ctx.target_path, install_ctx.game_data, logger)

    install_unreal5_patch(
        install_ctx.target_path,
        install_ctx.game_data,
        module_download_links,
        gpu_info,
        logger=logger,
        cached_archive_path=unreal5_cached_archive,
    )

    if install_ctx.fsr4_required:
        callbacks.install_fsr4_dll(install_ctx.target_path, install_ctx.fsr4_source_archive, logger)
    else:
        logger.info("Skipped FSR4 install for current GPU/game selection")

    install_ctx.handler.finalize_install(app, install_ctx.game_data, install_ctx.target_path, logger)

    rtss_notice.apply_rtss_global_settings_if_needed(logger=logger)

    logger.info("Install completed")
    installed_game = dict(install_ctx.game_data)
    installed_game["__installed_proxy_name__"] = str(install_ctx.final_dll_name or "")
    return installed_game


__all__ = [
    "InstallContext",
    "InstallWorkflowCallbacks",
    "build_install_context",
    "resolve_install_exclude_patterns",
    "run_install_workflow",
]
