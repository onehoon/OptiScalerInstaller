from __future__ import annotations

import ctypes
import locale
import logging
import os
from dataclasses import dataclass
from typing import Literal, Mapping


Lang = Literal["ko", "en"]
UI_LANGUAGE_ENV = "FORCE_UI_LANGUAGE"
_NO_AVAILABLE_DLL_PREFIX = "No available OptiScaler DLL names for installation. "
_CHECKED_PREFIX = "Checked: "


@dataclass(frozen=True)
class CommonStrings:
    ok: str
    notice: str
    warning: str
    error: str


@dataclass(frozen=True)
class MainUiStrings:
    heading_font_family: str
    ui_font_family: str
    window_title_template: str
    app_title: str
    gpu_label_template: str
    checking_gpu: str
    unknown_gpu: str
    status_game_db: str
    status_gpu_config: str
    status_gpu_select: str
    scan_section_title: str
    browse_button: str
    supported_games_link: str
    install_section_title: str
    install_button: str
    installing_button: str
    select_game_hint: str
    no_information: str
    version_line_template: str
    waiting_for_gpu_selection: str
    scanning: str
    scan_result_title: str
    auto_scan_no_results: str
    manual_scan_no_results: str


@dataclass(frozen=True)
class DialogStrings:
    installer_notice_title: str
    wiki_not_configured_detail: str
    wiki_open_failed_detail: str
    close_while_installing_body: str
    installation_completed: str
    installation_completed_with_name_template: str
    game_db_loading_title: str
    game_db_loading_body: str
    game_db_error_title: str
    game_db_error_body: str
    installing_title: str
    installing_body: str
    select_game_card_body: str
    preparing_archive_title: str
    preparing_archive_body: str
    precheck_incomplete_body: str
    precheck_retry_mods_body: str
    optiscaler_archive_not_ready: str
    invalid_game_body: str
    preparing_download_title: str
    preparing_download_body: str
    fsr4_not_ready: str
    confirm_popup_body: str
    install_failed_body_template: str


@dataclass(frozen=True)
class GpuStrings:
    unsupported_title: str
    unsupported_message: str
    dual_selection_title: str
    dual_selection_message: str
    vendor_unknown: str


@dataclass(frozen=True)
class RtssStrings:
    notice_title: str
    no_message: str
    fallback_message: str


@dataclass(frozen=True)
class UpdateStrings:
    available_title: str
    available_body_template: str


@dataclass(frozen=True)
class PrecheckStrings:
    no_available_dll: str
    checked_names_template: str
    mod_notice_header: str
    mod_notice_footer: str
    reshade_detected_template: str
    special_k_detected_template: str
    ultimate_asi_loader_detected_template: str
    renodx_detected_template: str
    mod_detected_template: str
    rdr2_blocked_mod_popup_template: str


@dataclass(frozen=True)
class AppStrings:
    lang: Lang
    common: CommonStrings
    main: MainUiStrings
    dialogs: DialogStrings
    gpu: GpuStrings
    rtss: RtssStrings
    update: UpdateStrings
    precheck: PrecheckStrings


_STRINGS_BY_LANG: dict[Lang, AppStrings] = {
    "ko": AppStrings(
        lang="ko",
        common=CommonStrings(
            ok="확인",
            notice="알림",
            warning="경고",
            error="오류",
        ),
        main=MainUiStrings(
            heading_font_family="Malgun Gothic",
            ui_font_family="Malgun Gothic",
            window_title_template="OptiScaler Installer v{version}",
            app_title="OptiScaler Installer",
            gpu_label_template="GPU: {gpu}",
            checking_gpu="GPU 확인 중...",
            unknown_gpu="알 수 없음",
            status_game_db="게임 DB",
            status_gpu_config="GPU 구성",
            status_gpu_select="GPU 선택",
            scan_section_title="1. 게임 폴더 스캔",
            browse_button="폴더 선택",
            supported_games_link="지원 게임 목록 보기",
            install_section_title="2. 설치 정보",
            install_button="설치",
            installing_button="설치 중...",
            select_game_hint="게임을 선택하면 설치 정보를 볼 수 있습니다.",
            no_information="표시할 정보가 없습니다.",
            version_line_template="OptiScaler 버전: {value}",
            waiting_for_gpu_selection="GPU 선택 대기 중",
            scanning="스캔 중...",
            scan_result_title="스캔 결과",
            auto_scan_no_results="자동 스캔에서 지원되는 게임을 찾지 못했습니다.\n게임이 설치된 폴더를 직접 선택해 주세요.",
            manual_scan_no_results="선택하신 폴더에서 지원되는 게임을 찾지 못했습니다.",
        ),
        dialogs=DialogStrings(
            installer_notice_title="설치 안내",
            wiki_not_configured_detail="지원 게임 위키 주소가 설정되지 않았습니다.",
            wiki_open_failed_detail="지원 게임 위키를 열지 못했습니다.",
            close_while_installing_body="설치가 진행 중입니다. 완료 후 종료해 주세요.",
            installation_completed="설치가 완료되었습니다.",
            installation_completed_with_name_template=(
                "[RED]OptiScaler가 설치되었습니다.\n"
                "삭제하시려면 OptiScaler 파일 ({name})을 삭제하거나 다른 이름으로 바꾸세요[END]"
            ),
            game_db_loading_title="게임 DB 로딩 중",
            game_db_loading_body="게임 DB를 불러오는 중입니다. 잠시만 기다려 주세요.",
            game_db_error_title="게임 DB 오류",
            game_db_error_body="게임 DB에 연결하지 못했습니다.\n인스톨러를 재실행 해주세요.",
            installing_title="설치 진행 중",
            installing_body="설치가 이미 진행 중입니다. 잠시만 기다려 주세요.",
            select_game_card_body="설치할 게임을 선택해 주세요.",
            preparing_archive_title="아카이브 준비 중",
            preparing_archive_body="OptiScaler 다운로드가 아직 진행 중입니다. 잠시만 기다려 주세요.",
            precheck_incomplete_body="OptiScaler DLL 호환성 확인이 아직 완료되지 않았습니다.",
            precheck_retry_mods_body="안내된 원인을 확인한 뒤 충돌할 수 있는 파일이나 설정을 정리하고 다시 설치해 주세요.",
            optiscaler_archive_not_ready="OptiScaler 아카이브가 아직 준비되지 않았습니다.",
            invalid_game_body="유효한 게임 항목을 선택해 주세요.",
            preparing_download_title="다운로드 준비 중",
            preparing_download_body="FSR4 다운로드가 아직 진행 중입니다. 잠시만 기다려 주세요.",
            fsr4_not_ready="FSR4가 아직 준비되지 않았습니다.",
            confirm_popup_body="설치 전에 안내 팝업을 먼저 확인해 주세요.",
            install_failed_body_template="설치 중 오류가 발생했습니다: {message}",
        ),
        gpu=GpuStrings(
            unsupported_title="지원되지 않는 GPU 구성",
            unsupported_message="3개 이상의 GPU가 감지되었습니다.\n현재 설치는 지원되지 않습니다.",
            dual_selection_title="GPU 선택",
            dual_selection_message=(
                "듀얼 GPU가 감지되었습니다.\n"
                "OptiScaler를 어느 GPU 기준으로 설치할지 선택해 주세요.\n"
                "선택한 GPU에 맞는 설정으로 설치됩니다.\n"
                "다른 GPU로 실행 시 정상적으로 동작하지 않을 수 있습니다."
            ),
            vendor_unknown="알 수 없음",
        ),
        rtss=RtssStrings(
            notice_title="RTSS 안내",
            no_message="표시할 안내 메시지가 없습니다.",
            fallback_message=(
                "RTSS 설정을 확인해주세요.\n\n"
                "[Global]\n"
                "UseDetours=1\n"
                "ReflexSetLatencyMarker=0\n\n"
                "위 설정이 적용되어 있는지 확인해 주세요."
            ),
        ),
        update=UpdateStrings(
            available_title="업데이트 확인",
            available_body_template="최신 버전(v{version})이 있습니다.\n지금 업데이트하시겠습니까?",
        ),
        precheck=PrecheckStrings(
            no_available_dll="설치에 사용할 수 있는 OptiScaler DLL 이름이 없습니다.",
            checked_names_template="확인한 이름: {names}",
            mod_notice_header=(
                "[RED]다른 MOD가 감지되었습니다.\n"
                "감지된 항목에 따라 일부는 자동으로 호환 처리될 수 있지만,\n"
                "MOD에 따라 게임이 실행되지 않거나 오동작할 수 있습니다.[END]"
            ),
            mod_notice_footer="감지된 항목을 확인한 뒤 설치를 계속하려면 메인 창의 설치 버튼을 눌러 진행해 주세요.",
            reshade_detected_template="ReShade: {detected}\n\nReShade({detected})와의 호환성을 위해 OptiScaler는 다른 이름으로 설치됩니다.",
            special_k_detected_template="Special K: {detected}",
            ultimate_asi_loader_detected_template="Ultimate ASI Loader: {detected}",
            renodx_detected_template="RenoDX: {detected}",
            mod_detected_template="MOD: {detected}",
            rdr2_blocked_mod_popup_template="[RED][DOT]RDR2는 일부 비호환 MOD가 감지되면 설치를 진행할 수 없습니다.[BR][DOT]아래 항목을 정리한 뒤 다시 시도해 주세요.[BR][DOT]감지된 비호환 MOD는 다음과 같습니다.[END][BR]{mods}",
        ),
    ),
    "en": AppStrings(
        lang="en",
        common=CommonStrings(
            ok="OK",
            notice="Notice",
            warning="Warning",
            error="Error",
        ),
        main=MainUiStrings(
            heading_font_family="Segoe UI",
            ui_font_family="Segoe UI",
            window_title_template="OptiScaler Installer v{version}",
            app_title="OptiScaler Installer",
            gpu_label_template="GPU: {gpu}",
            checking_gpu="Checking GPU...",
            unknown_gpu="Unknown",
            status_game_db="Game DB",
            status_gpu_config="GPU Config",
            status_gpu_select="GPU Select",
            scan_section_title="1. Scan Game Folder",
            browse_button="Browse...",
            supported_games_link="Check Supported Games",
            install_section_title="2. Install Information",
            install_button="Install",
            installing_button="Installing...",
            select_game_hint="Select a game to view information.",
            no_information="No information available.",
            version_line_template="OptiScaler Version: {value}",
            waiting_for_gpu_selection="Waiting for GPU selection",
            scanning="Scanning...",
            scan_result_title="Scan Result",
            auto_scan_no_results="No supported games were found during automatic scan.\nPlease choose your game installation folder manually.",
            manual_scan_no_results="No supported games found in the selected folder.",
        ),
        dialogs=DialogStrings(
            installer_notice_title="Notice",
            wiki_not_configured_detail="Supported games wiki URL is not configured.",
            wiki_open_failed_detail="Failed to open the supported games wiki.",
            close_while_installing_body="Installation is in progress. Please wait.",
            installation_completed="Installation completed.",
            installation_completed_with_name_template=(
                "[RED]OptiScaler has been installed.\n"
                "To remove it, delete or rename the OptiScaler file ({name}).[END]"
            ),
            game_db_loading_title="Game DB Loading",
            game_db_loading_body="Game DB is still loading. Please wait a moment.",
            game_db_error_title="Game DB Error",
            game_db_error_body="Failed to connect to Game DB.\nPlease check network or restart Installer.",
            installing_title="Installing",
            installing_body="Installation is already in progress. Please wait.",
            select_game_card_body="Please select a game card to install.",
            preparing_archive_title="Preparing Archive",
            preparing_archive_body="OptiScaler archive download is still in progress. Please wait.",
            precheck_incomplete_body="OptiScaler DLL compatibility check has not completed.",
            precheck_retry_mods_body="Review the message above, then remove or adjust any conflicting files or settings before trying again.",
            optiscaler_archive_not_ready="OptiScaler archive is not ready yet.",
            invalid_game_body="Please select a valid game item.",
            preparing_download_title="Preparing Download",
            preparing_download_body="FSR4 download is still in progress. Please wait.",
            fsr4_not_ready="FSR4 is not ready yet.",
            confirm_popup_body="Please confirm the popup before installing.",
            install_failed_body_template="An error occurred during installation: {message}",
        ),
        gpu=GpuStrings(
            unsupported_title="Unsupported GPU Configuration",
            unsupported_message="3 or more GPUs were detected.\nThis installation is not supported.",
            dual_selection_title="GPU Selection",
            dual_selection_message=(
                "Dual GPUs were detected.\n"
                "Select which GPU OptiScaler should be installed for.\n"
                "Installation will use settings for the selected GPU.\n"
                "It may not work correctly if the game is run on the other GPU."
            ),
            vendor_unknown="Unknown",
        ),
        rtss=RtssStrings(
            notice_title="RTSS Notice",
            no_message="(No message)",
            fallback_message=(
                "RTSS Configuration Check:\n\n"
                "Please ensure the following settings in your Global profile:\n"
                "UseDetours=1\n"
                "ReflexSetLatencyMarker=0"
            ),
        ),
        update=UpdateStrings(
            available_title="Update Available",
            available_body_template="A new version (v{version}) is available.\nDo you want to update now?",
        ),
        precheck=PrecheckStrings(
            no_available_dll="No available OptiScaler DLL names for installation.",
            checked_names_template="Checked: {names}",
            mod_notice_header=(
                "[RED]Other MODs were detected.\n"
                "Some detected MODs may be handled automatically,\n"
                "but certain MODs can prevent the game from launching or cause unexpected behavior.[END]"
            ),
            mod_notice_footer="After reviewing the detected MODs, press the Install button in the main window to continue.",
            reshade_detected_template="ReShade: {detected}\n\nFor ReShade ({detected}) compatibility, OptiScaler will be installed with a different name.",
            special_k_detected_template="Special K: {detected}",
            ultimate_asi_loader_detected_template="Ultimate ASI Loader: {detected}",
            renodx_detected_template="RenoDX: {detected}",
            mod_detected_template="MOD: {detected}",
            rdr2_blocked_mod_popup_template="[RED][DOT]RDR2 installation cannot continue when certain incompatible MODs are detected.[BR][DOT]Remove the items below and try again.[BR][DOT]The following incompatible MODs were detected:[END][BR]{mods}",
        ),
    ),
}


def get_app_strings(lang: Lang) -> AppStrings:
    return _STRINGS_BY_LANG[lang]


def lang_from_bool(use_korean: bool) -> Lang:
    return "ko" if use_korean else "en"


def is_korean(lang: Lang) -> bool:
    return lang == "ko"


def _get_forced_ui_language() -> Lang | None:
    raw = str(os.environ.get(UI_LANGUAGE_ENV, "") or "").strip().lower()
    if raw in {"", "auto"}:
        return None
    if raw in {"ko", "kr", "korean"}:
        return "ko"
    if raw in {"en", "english"}:
        return "en"

    logging.warning("[APP] Invalid %s=%r, using automatic UI language detection", UI_LANGUAGE_ENV, raw)
    return None


def detect_ui_language() -> Lang:
    forced = _get_forced_ui_language()
    if forced is not None:
        logging.info("[APP] UI language forced by %s=%s", UI_LANGUAGE_ENV, forced.upper())
        return forced

    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0xFF) == 0x12:
            return "ko"
    except Exception:
        pass

    try:
        lang = locale.getlocale()[0] or ""
        if lang.lower().startswith("ko"):
            return "ko"
    except Exception:
        pass

    return "en"


def _sheet_lang_suffix(lang: Lang) -> str:
    return "kr" if lang == "ko" else "en"


def pick_sheet_text(source: Mapping[str, object], base_key: str, lang: Lang) -> str:
    key = f"{base_key}_{_sheet_lang_suffix(lang)}"
    return str(source.get(key, "") or "").strip()


def pick_module_message(source: Mapping[str, object], base_key: str, lang: Lang) -> str:
    key = f"__{base_key}_{_sheet_lang_suffix(lang)}__"
    return str(source.get(key, "") or "").strip()


def translate_default_precheck_error(raw_error: str, lang: Lang) -> str:
    error_text = str(raw_error or "").strip()
    if lang != "ko" or not error_text.startswith(_NO_AVAILABLE_DLL_PREFIX):
        return error_text

    strings = get_app_strings(lang)
    checked_names = error_text.split(_CHECKED_PREFIX, 1)[1] if _CHECKED_PREFIX in error_text else ""
    translated = strings.precheck.no_available_dll
    if checked_names:
        translated = f"{translated} {strings.precheck.checked_names_template.format(names=checked_names)}"
    return translated


def build_mod_conflict_finding_text(kind: str, detected: str, lang: Lang) -> str:
    strings = get_app_strings(lang)
    templates = {
        "reshade": strings.precheck.reshade_detected_template,
        "special_k": strings.precheck.special_k_detected_template,
        "ultimate_asi_loader": strings.precheck.ultimate_asi_loader_detected_template,
        "renodx": strings.precheck.renodx_detected_template,
    }
    template = templates.get(kind, strings.precheck.mod_detected_template)
    return template.format(detected=detected)


def build_mod_conflict_notice_text(lines: list[str], lang: Lang) -> str:
    if not lines:
        return ""
    strings = get_app_strings(lang)
    return "\n".join(
        [
            strings.precheck.mod_notice_header,
            "",
            *(f"[INDENT][DOT]{line}" for line in lines),
            "",
            strings.precheck.mod_notice_footer,
        ]
    ).strip()


__all__ = [
    "AppStrings",
    "Lang",
    "UI_LANGUAGE_ENV",
    "build_mod_conflict_finding_text",
    "build_mod_conflict_notice_text",
    "detect_ui_language",
    "get_app_strings",
    "is_korean",
    "lang_from_bool",
    "pick_module_message",
    "pick_sheet_text",
    "translate_default_precheck_error",
]
