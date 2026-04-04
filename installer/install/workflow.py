from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..config import ini_utils
from ..games.handlers import get_game_handler
from . import services as installer_services
from .components import (
    OPTISCALER_ASI_NAME,
    install_optipatcher,
    install_reframework_dinput8,
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
    logger,
) -> InstallContext:
    handler = get_game_handler(game_data)
    logger.info("Using game handler: %s", getattr(handler, "handler_key", "default"))

    install_plan = handler.prepare_install_plan(app, game_data, source_archive, resolved_dll_name, logger)
    planned_game_data = dict(install_plan.game_data)
    planned_source_archive = str(install_plan.source_archive or source_archive)
    planned_resolved_dll_name = str(install_plan.resolved_dll_name or resolved_dll_name)
    target_path = planned_game_data["path"]
    use_ultimate_asi_loader = bool(planned_game_data.get("ultimate_asi_loader"))

    if use_ultimate_asi_loader and planned_game_data.get("reframework_url"):
        raise RuntimeError(
            "Ultimate ASI Loader and REFramework both require dinput8.dll, and this combination is not supported yet."
        )

    if use_ultimate_asi_loader:
        final_dll_name = planned_resolved_dll_name or OPTISCALER_ASI_NAME
        logger.info("Install mode: Ultimate ASI Loader (%s)", final_dll_name)
    else:
        final_dll_name = installer_services.resolve_proxy_dll_name(
            target_path,
            planned_resolved_dll_name or str(planned_game_data.get("dll_name", "")).strip(),
            logger=logger,
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
    )


def run_install_workflow(
    app: Any,
    install_ctx: InstallContext,
    module_download_links: Mapping[str, object],
    optipatcher_url: str,
    gpu_info: Any,
    callbacks: InstallWorkflowCallbacks,
    logger,
) -> dict[str, Any]:
    logger.info("Install started: target=%s", install_ctx.target_path)
    exclude_patterns = resolve_install_exclude_patterns(module_download_links)
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
        install_ultimate_asi_loader(install_ctx.target_path, module_download_links, logger=logger)

    merged_ini_settings = dict(install_ctx.game_data.get("ini_settings", {}))
    install_reframework_dinput8(install_ctx.target_path, install_ctx.game_data, logger=logger)
    merged_ini_settings.update(
        install_optipatcher(
            install_ctx.target_path,
            install_ctx.game_data,
            module_download_links,
            str(optipatcher_url or ""),
            logger=logger,
        )
    )

    ini_utils.apply_ini_settings(ini_path, merged_ini_settings, force_frame_generation=True, logger=logger)
    logger.info("Applied ini settings to %s", ini_path)

    callbacks.apply_optional_ingame_ini_settings(install_ctx.target_path, install_ctx.game_data, logger)
    callbacks.apply_optional_engine_ini_settings(install_ctx.target_path, install_ctx.game_data, logger)

    install_unreal5_patch(
        install_ctx.target_path,
        install_ctx.game_data,
        module_download_links,
        gpu_info,
        logger=logger,
    )

    if install_ctx.fsr4_required:
        callbacks.install_fsr4_dll(install_ctx.target_path, install_ctx.fsr4_source_archive, logger)
    else:
        logger.info("Skipped FSR4 install for current GPU/game selection")

    install_ctx.handler.finalize_install(app, install_ctx.game_data, install_ctx.target_path, logger)
    logger.info("Install completed")
    return dict(install_ctx.game_data)


__all__ = [
    "InstallContext",
    "InstallWorkflowCallbacks",
    "build_install_context",
    "resolve_install_exclude_patterns",
    "run_install_workflow",
]
