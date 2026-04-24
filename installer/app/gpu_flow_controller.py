from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
import logging
from typing import Any

from ..system import gpu_service
from ..common import schedule_safely


SchedulerCallback = Callable[[Callable[[], None]], Any]
GpuContextDetector = Callable[[], gpu_service.GpuContext]
GpuAdapterSelector = Callable[[tuple[gpu_service.GpuAdapterChoice, ...]], gpu_service.GpuAdapterChoice | None]


@dataclass(frozen=True)
class GpuFlowState:
    gpu_context: gpu_service.GpuContext
    gpu_names: tuple[str, ...]
    gpu_count: int
    is_multi_gpu: bool
    multi_gpu_blocked: bool
    gpu_info: str
    gpu_selection_pending: bool
    selected_adapter: gpu_service.GpuAdapterChoice | None
    game_db_vendor: str | None


@dataclass(frozen=True)
class GpuFlowCallbacks:
    apply_state: Callable[[GpuFlowState], None]
    handle_unsupported_gpu: Callable[[str, str], None]
    set_scan_status_message: Callable[[str, str], None]
    update_sheet_status: Callable[[], None]
    update_install_button_state: Callable[[], None]
    start_game_db_load: Callable[[], None]


class GpuFlowController:
    def __init__(
        self,
        *,
        executor: Executor,
        schedule: SchedulerCallback,
        callbacks: GpuFlowCallbacks,
        unknown_gpu_text: str,
        waiting_for_gpu_selection_text: str,
        unsupported_gpu_message: str,
        unsupported_gpu_info_text: str,
        detect_gpu_context: GpuContextDetector,
        select_dual_gpu_adapter: GpuAdapterSelector,
        show_unsupported_gpu_notice: Callable[[], None] = lambda: None,
        max_supported_gpu_count: int = 2,
        logger=None,
    ) -> None:
        self._executor = executor
        self._schedule = schedule
        self._callbacks = callbacks
        self._unknown_gpu_text = str(unknown_gpu_text or "").strip() or "Unknown GPU"
        self._waiting_for_gpu_selection_text = str(waiting_for_gpu_selection_text or "").strip() or self._unknown_gpu_text
        self._unsupported_gpu_message = str(unsupported_gpu_message or "").strip()
        self._unsupported_gpu_info_text = str(unsupported_gpu_info_text or "").strip()
        self._detect_gpu_context = detect_gpu_context
        self._select_dual_gpu_adapter = select_dual_gpu_adapter
        self._show_unsupported_gpu_notice = show_unsupported_gpu_notice
        self._max_supported_gpu_count = max(1, int(max_supported_gpu_count))
        self._logger = logger or logging.getLogger()

        self._unsupported_notice_shown = False

    def start_detection(self) -> bool:
        try:
            self._executor.submit(self._run_detect_worker)
        except Exception:
            self._logger.exception("Failed to submit GPU info fetch task")
            return False
        return True

    def _run_detect_worker(self) -> None:
        try:
            gpu_context = self._detect_gpu_context()
        except Exception:
            self._logger.exception("Error fetching GPU info")
            gpu_context = gpu_service.GpuContext(
                gpu_names=[],
                gpu_count=0,
                gpu_info=self._unknown_gpu_text,
                selected_vendor="default",
                adapters=(),
                selected_model_name="",
            )

        schedule_safely(
            self._schedule,
            lambda detected_context=gpu_context: self._on_gpu_context_detected(detected_context),
            self._logger,
            description="GPU flow update callback",
        )

    def _normalize_gpu_info_text(self, value: object) -> str:
        text = str(value or "").strip()
        if not text or text.lower().startswith("unknown"):
            return self._unknown_gpu_text
        return text

    def _build_state(
        self,
        gpu_context: gpu_service.GpuContext,
        *,
        selected_adapter: gpu_service.GpuAdapterChoice | None = None,
        blocked: bool = False,
        selection_pending: bool = False,
    ) -> GpuFlowState:
        gpu_names = tuple(gpu_context.gpu_names or [])
        gpu_count = max(0, int(gpu_context.gpu_count or 0))
        is_multi_gpu = bool(gpu_context.is_multi_gpu)

        if blocked:
            gpu_info = self._normalize_gpu_info_text(gpu_context.gpu_info)
            return GpuFlowState(
                gpu_context=gpu_context,
                gpu_names=gpu_names,
                gpu_count=gpu_count,
                is_multi_gpu=is_multi_gpu,
                multi_gpu_blocked=True,
                gpu_info=gpu_info,
                gpu_selection_pending=False,
                selected_adapter=None,
                game_db_vendor=None,
            )

        if selection_pending:
            return GpuFlowState(
                gpu_context=gpu_context,
                gpu_names=gpu_names,
                gpu_count=gpu_count,
                is_multi_gpu=is_multi_gpu,
                multi_gpu_blocked=False,
                gpu_info=self._waiting_for_gpu_selection_text,
                gpu_selection_pending=True,
                selected_adapter=None,
                game_db_vendor=None,
            )

        if selected_adapter is not None:
            gpu_info = self._normalize_gpu_info_text(
                selected_adapter.model_name or gpu_context.selected_model_name or gpu_context.gpu_info
            )
            game_db_vendor = str(selected_adapter.vendor or "default")
        else:
            gpu_info = self._normalize_gpu_info_text(gpu_context.selected_model_name or gpu_context.gpu_info)
            game_db_vendor = str(gpu_context.selected_vendor or "default")

        return GpuFlowState(
            gpu_context=gpu_context,
            gpu_names=gpu_names,
            gpu_count=gpu_count,
            is_multi_gpu=is_multi_gpu,
            multi_gpu_blocked=False,
            gpu_info=gpu_info,
            gpu_selection_pending=False,
            selected_adapter=selected_adapter,
            game_db_vendor=game_db_vendor,
        )

    def _apply_selected_state(self, state: GpuFlowState) -> None:
        self._callbacks.apply_state(state)
        self._callbacks.set_scan_status_message("", "")
        self._callbacks.update_sheet_status()
        self._callbacks.update_install_button_state()

    def _on_gpu_context_detected(self, gpu_context: gpu_service.GpuContext) -> None:
        try:
            gpu_count = max(0, int(gpu_context.gpu_count or 0))

            if gpu_count > self._max_supported_gpu_count:
                state = self._build_state(gpu_context, blocked=True)
                self._callbacks.apply_state(state)
                self._callbacks.handle_unsupported_gpu(
                    self._unsupported_gpu_message,
                    self._unsupported_gpu_info_text,
                )
                if not self._unsupported_notice_shown:
                    self._unsupported_notice_shown = True
                    self._show_unsupported_gpu_notice()
                return

            if gpu_count == 2 and len(gpu_context.adapters or ()) >= 2:
                pending_state = self._build_state(gpu_context, selection_pending=True)
                self._apply_selected_state(pending_state)
                selected_adapter = self._select_dual_gpu_adapter(tuple(gpu_context.adapters[:2]))
                if selected_adapter is None:
                    # Defensive fallback: normal UI flow should not allow closing the modal without a choice.
                    # If this branch is hit, keep pending state and avoid starting DB loading.
                    self._logger.warning("[GPU] Dual-GPU selection popup closed without a selection")
                    return

                selected_state = self._build_state(gpu_context, selected_adapter=selected_adapter)
                self._apply_selected_state(selected_state)
                self._callbacks.start_game_db_load()
                return

            selected_state = self._build_state(gpu_context)
            self._apply_selected_state(selected_state)
            self._callbacks.start_game_db_load()
        except Exception:
            self._logger.exception("Failed to update GPU flow")
