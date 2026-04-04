"""UI and application-facing helpers."""

from . import gpu_notice, message_popup, rtss_notice
from .archive_controller import ArchivePreparationCallbacks, ArchivePreparationController, ArchivePreparationState
from .app_actions_controller import AppActionCallbacks, AppActionsController
from .app_shutdown_controller import AppShutdownCallbacks, AppShutdownController, AppShutdownStep
from .card_factory import GameCardBuildResult, GameCardTheme, create_game_card
from .card_layout import (
    CardOverflowFitDecision,
    CardResizeReflowDecision,
    compute_card_overflow_fit_decision,
    compute_card_resize_reflow_decision,
)
from .card_render_controller import CardRenderCallbacks, CardRenderController
from .card_grid import (
    CardGridPlacement,
    build_card_grid_placements,
    clamp_grid_columns,
    compute_visible_game_indices,
    get_card_grid_placement,
)
from .card_visuals import (
    GameCardVisualTheme,
    ensure_game_card_image_cache,
    render_game_card_visual,
    update_game_card_base_image,
)
from .game_db_controller import GameDbControllerCallbacks, GameDbLoadController, GameDbLoadResult
from .gpu_flow_controller import GpuFlowCallbacks, GpuFlowController, GpuFlowState
from .install_selection_controller import (
    InstallSelectionCallbacks,
    InstallSelectionController,
    InstallSelectionPrecheckOutcome,
    InstallSelectionUiState,
)
from .install_state import (
    SelectedGameSnapshot,
    build_install_button_state_inputs,
    build_install_entry_state,
    build_selected_game_snapshot,
)
from .install_ui_state import InstallButtonState, InstallButtonStateInputs, compute_install_button_state
from .install_entry import InstallEntryDecision, InstallEntryState, validate_install_entry
from .message_popup import MessagePopupTheme, show_message_popup
from .notice_controller import AppNoticeController
from .popup_markup import (
    create_popup_markup_text,
    estimate_wrapped_text_lines,
    render_markup_to_text_widget,
    strip_markup_text,
)
from .popup_utils import PopupFadeController, create_modal_popup, present_modal_popup
from .scan_feedback import ScanFeedbackCallbacks, ScanFeedbackController
from .scan_entry_controller import ScanEntryCallbacks, ScanEntryController, ScanEntryState
from .scan_controller import ScanController, ScanControllerCallbacks
from .startup_flow import StartupFlowController, StartupFlowCallbacks
from .ui_presenters import BottomPanelPresenter, HeaderStatusPresenter

__all__ = [
    "ArchivePreparationCallbacks",
    "ArchivePreparationController",
    "ArchivePreparationState",
    "AppNoticeController",
    "AppActionCallbacks",
    "AppActionsController",
    "AppShutdownCallbacks",
    "AppShutdownController",
    "AppShutdownStep",
    "BottomPanelPresenter",
    "CardOverflowFitDecision",
    "CardRenderCallbacks",
    "CardRenderController",
    "CardResizeReflowDecision",
    "GameCardBuildResult",
    "GameCardTheme",
    "GameCardVisualTheme",
    "CardGridPlacement",
    "GameDbControllerCallbacks",
    "GameDbLoadController",
    "GameDbLoadResult",
    "GpuFlowCallbacks",
    "GpuFlowController",
    "GpuFlowState",
    "InstallEntryDecision",
    "InstallEntryState",
    "InstallButtonState",
    "InstallButtonStateInputs",
    "InstallSelectionCallbacks",
    "InstallSelectionController",
    "InstallSelectionPrecheckOutcome",
    "InstallSelectionUiState",
    "SelectedGameSnapshot",
    "MessagePopupTheme",
    "PopupFadeController",
    "ScanController",
    "ScanControllerCallbacks",
    "ScanEntryCallbacks",
    "ScanEntryController",
    "ScanEntryState",
    "ScanFeedbackCallbacks",
    "ScanFeedbackController",
    "StartupFlowController",
    "StartupFlowCallbacks",
    "HeaderStatusPresenter",
    "create_modal_popup",
    "create_popup_markup_text",
    "estimate_wrapped_text_lines",
    "gpu_notice",
    "message_popup",
    "build_install_button_state_inputs",
    "build_install_entry_state",
    "build_selected_game_snapshot",
    "create_game_card",
    "compute_card_overflow_fit_decision",
    "compute_card_resize_reflow_decision",
    "build_card_grid_placements",
    "clamp_grid_columns",
    "compute_visible_game_indices",
    "ensure_game_card_image_cache",
    "present_modal_popup",
    "compute_install_button_state",
    "get_card_grid_placement",
    "render_game_card_visual",
    "render_markup_to_text_widget",
    "rtss_notice",
    "show_message_popup",
    "strip_markup_text",
    "update_game_card_base_image",
    "validate_install_entry",
]
