import json
import os
import re
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
GAME_MASTER_PATH = ROOT_DIR / "assets" / "data" / "game_master.json"
WIKI_REPO_URL = "https://github.com/onehoon/OptiScalerInstaller.wiki.git"
NATIVE_XEFG_TEXT = "Native XeFG Support"
NEW_GAMES_HEADING = "## 신규 지원 게임 추가 / Newly Supported Games"
NEW_GAMES_METADATA_START = "<!-- newly-supported-games"
NEW_GAMES_METADATA_END = "-->"
LEGACY_NEW_GAMES_TABLE_HEADER = "| Korean Title | English Title |"
NEW_GAMES_TABLE_HEADER = "| Korean Title | English Title | Intel | AMD | NVIDIA |"
NEW_GAMES_TABLE_SEPARATOR = "|---|---|---|---|---|"
NEW_GAMES_HEADING_ALIASES = {
    NEW_GAMES_HEADING,
    "## 신규 지원 게임 추가 / Newly Added Supported Games",
}
SUPPORTED_GAMES_TABLE_HEADER = "| Korean Title | English Title | Intel | AMD | NVIDIA |"
SUPPORTED_GAMES_TABLE_SEPARATOR = "|---|---|---|---|---|"
WIKI_CAUTION_BLOCKS = [
    "> [!CAUTION]",
    "> 각종 MOD 사용 시 게임 실행이 불가능하거나 호환되지 않을 수 있습니다.",
    "> - ReShade, Special K, RenoDX 등",
    "> - 이 경우 OptiScaler를 수동으로 직접 설치하시기 바랍니다.",
    "> 게임 성능은 GPU 모델 및 게임 옵션에 따라 달라질 수 있습니다.",
    "> 사용하는 PC 환경에 따라 OptiScaler가 정상적으로 동작하지 않을 수 있습니다.",
    "",
    "> [!CAUTION]",
    "> Using certain mods may prevent the game from launching or cause compatibility issues.  ",
    "> - ReShade, Special K, RenoDX etc.",
    "> - In this case, please install OptiScaler manually.",
    "> Game performance may vary depending on your GPU model and game options.",
    "> OptiScaler may not work properly depending on your PC environment.",
]
RADEON_IGPU_NOTE_BLOCKS = [
    "> [!NOTE]",
    "> AMD Radeon iGPU* in this list refers to Radeon 780M, 880M, 890M, and 8060S.",
    "> 이 리스트에서 AMD Radeon iGPU* 지원은 Radeon 780M, 880M, 890M, 8060S를 의미합니다.",
]
WIKI_PUSH_TOKEN = str(os.environ.get("WIKI_PUSH_TOKEN", "") or "").strip()
TARGET_WIKI_PAGE_FILE = str(
    os.environ.get("TARGET_WIKI_PAGE_FILE", "Supported-Game-List-Test.md") or ""
).strip()
BASELINE_WIKI_PAGE_FILE = str(
    os.environ.get("BASELINE_WIKI_PAGE_FILE", "Supported-Game-List.md") or ""
).strip()


def require_env_value(name: str) -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def require_int_env_value(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if value != value:
        return ""
    return str(value).strip()


def normalize_header(value: Any) -> str:
    return normalize_text(value).lstrip("\ufeff").strip().lower()


def escape_md(text: Any) -> str:
    return normalize_text(text).replace("|", "\\|").replace("\n", " ")


def unescape_md_table_cell(text: Any) -> str:
    return normalize_text(text).replace("\\|", "|")


def parse_bool(value: Any, default: bool = False) -> bool:
    normalized = normalize_text(value).lower()
    if not normalized:
        return bool(default)
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def is_new_games_heading(line: str) -> bool:
    return normalize_text(line) in NEW_GAMES_HEADING_ALIASES


def split_gpu_tokens(value: Any) -> list[str]:
    text = normalize_text(value)
    if not text:
        return []
    return [token.strip() for token in text.split("|") if token.strip()]


def compact_series_label(prefix: str, series: list[str]) -> str:
    ordered: list[str] = []
    for value in series:
        normalized = normalize_text(value)
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return f"{prefix} {ordered[0]} Series"
    return f"{prefix} {'/'.join(ordered)} Series"


def parse_spreadsheet_url(spreadsheet_url: str) -> tuple[str, str]:
    parsed = urlparse(spreadsheet_url)
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path)
    if not match:
        raise RuntimeError("INSTALL_PROFILE_SHEET_URL does not contain a valid spreadsheet id.")

    spreadsheet_id = match.group(1)
    query_params = parse_qs(parsed.query)
    gid = normalize_text(query_params.get("gid", [""])[0])

    if not gid and parsed.fragment:
        if parsed.fragment.startswith("gid="):
            gid = normalize_text(parsed.fragment.split("=", 1)[1])
        else:
            fragment_params = parse_qs(parsed.fragment)
            gid = normalize_text(fragment_params.get("gid", [""])[0])

    if not gid:
        raise RuntimeError("INSTALL_PROFILE_SHEET_URL does not contain a gid.")

    return spreadsheet_id, gid


def build_google_session() -> requests.Session:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    service_account_info = json.loads(require_env_value("GCP_SERVICE_ACCOUNT_KEY"))
    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    credentials.refresh(Request())

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {credentials.token}"})
    return session


def fetch_sheet_title(session: requests.Session, spreadsheet_id: str, gid: str) -> str:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        params={"fields": "sheets(properties(sheetId,title))"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    for sheet in payload.get("sheets", []):
        properties = sheet.get("properties") or {}
        if normalize_text(properties.get("sheetId")) == normalize_text(gid):
            title = normalize_text(properties.get("title"))
            if title:
                return title

    raise RuntimeError(f"Could not find a sheet tab for gid={gid}.")


def fetch_sheet_rows(session: requests.Session, spreadsheet_id: str, sheet_title: str) -> list[dict[str, str]]:
    quoted_title = sheet_title.replace("'", "''")
    range_expr = f"'{quoted_title}'"
    encoded_range = quote(range_expr, safe="")
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    values = payload.get("values") or []
    if not values:
        return []

    headers = [normalize_header(cell) for cell in values[0]]
    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = {
            headers[index]: normalize_text(raw_row[index]) if index < len(raw_row) else ""
            for index in range(len(headers))
            if headers[index]
        }
        if any(normalize_text(value) for value in row.values()):
            rows.append(row)
    return rows


def is_test_sheet_row(row: dict[str, str]) -> bool:
    targets = (
        normalize_text(row.get("profile_id")),
        normalize_text(row.get("game_id")),
    )
    return any("test" in target.lower() for target in targets if target)


def is_test_game_title(*titles: Any) -> bool:
    keywords = ("test", "테스트")
    for title in titles:
        text = normalize_text(title).lower()
        if text and any(keyword in text for keyword in keywords):
            return True
    return False


def normalize_vendor(value: Any) -> str:
    text = normalize_text(value).upper()
    if text in {"INTEL", "AMD", "NVIDIA", "ALL"}:
        return text
    return ""


def load_install_profile_rows() -> list[dict[str, str]]:
    spreadsheet_url = require_env_value("INSTALL_PROFILE_SHEET_URL")
    spreadsheet_id, gid = parse_spreadsheet_url(spreadsheet_url)
    session = build_google_session()
    sheet_title = fetch_sheet_title(session, spreadsheet_id, gid)
    print("Resolved install_profile tab from the configured sheet secret.")
    return fetch_sheet_rows(session, spreadsheet_id, sheet_title)


def load_game_master() -> dict[str, dict[str, Any]]:
    payload = json.loads(GAME_MASTER_PATH.read_text(encoding="utf-8"))
    games: dict[str, dict[str, Any]] = {}
    for raw_game in payload:
        if not isinstance(raw_game, dict):
            continue
        if not parse_bool(raw_game.get("enabled"), default=False):
            continue
        game_id = normalize_text(raw_game.get("game_id"))
        if not game_id:
            continue
        if is_test_game_title(raw_game.get("game_name_kr"), raw_game.get("game_name_en")):
            continue
        games[game_id] = dict(raw_game)
    return games


def build_sheet_index(rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, list[str]]], set[str]]:
    indexed: dict[str, dict[str, list[str]]] = {}
    all_supported_games: set[str] = set()

    for row in rows:
        if is_test_sheet_row(row):
            continue
        if not parse_bool(row.get("enabled"), default=True):
            continue

        game_id = normalize_text(row.get("game_id"))
        if not game_id:
            continue

        vendor = normalize_vendor(row.get("gpu_vendor"))
        if not vendor:
            print(f"[WARN] Skipping row with unknown gpu_vendor for game_id={game_id}")
            continue

        model_rule = normalize_text(row.get("gpu_model_match"))
        if vendor == "ALL":
            if model_rule.lower() in {"", "all"}:
                all_supported_games.add(game_id)
            else:
                print(f"[WARN] Ignoring ALL vendor row with non-all gpu_model_match for game_id={game_id}: {model_rule}")
            continue

        vendor_map = indexed.setdefault(game_id, {"INTEL": [], "AMD": [], "NVIDIA": []})
        if model_rule:
            vendor_map[vendor].append(model_rule)

    return indexed, all_supported_games


def build_intel_label(tokens: list[str]) -> str:
    arc_found = False
    models = []
    for token in tokens:
        upper = token.upper()
        stripped = upper.strip('*')
        if "ARC" in upper:
            arc_found = True
        elif stripped in {"130V", "140V", "130T", "140T"}:
            models.append(stripped)
    if arc_found:
        # ARC와 세부 모델이 동시에 있으면 Arc Series만 표기
        return "Intel Arc Series"
    if models:
        if len(models) == 1:
            return f"Intel {models[0]}"
        else:
            return "Intel Arc " + "/".join(models)
    return ""


def build_amd_label(tokens: list[str]) -> str:
    labels: list[str] = []
    rx_series: list[str] = []
    has_radeon_igpu = False

    def add(label: str) -> None:
        if label not in labels:
            labels.append(label)

    def add_rx(label: str) -> None:
        if label not in rx_series:
            rx_series.append(label)

    for token in tokens:
        upper = token.upper()
        if upper in {"*780M*", "*880M*", "*890M*", "*8060S*"}:
            has_radeon_igpu = True
        elif upper in {"*RX 6*", "*RX 60*"}:
            add_rx("6000")
        elif upper in {"*RX 7*", "*RX 70*"}:
            add_rx("7000")
        elif upper in {"*RX 9*", "*RX 90*"}:
            add_rx("9000")

    if has_radeon_igpu:
        add("Radeon iGPU*")

    rx_label = compact_series_label("RX", rx_series)
    if rx_label:
        add(rx_label)

    return ", ".join(labels)


def build_nvidia_label(tokens: list[str]) -> str:
    series_map = {"2": "20", "3": "30", "4": "40", "5": "50"}
    found: list[str] = []

    for token in tokens:
        upper = token.upper()
        match = re.match(r"\*RTX\s*(\d{2})\*", upper)
        if not match:
            continue
        key = match.group(1)[0]
        label = series_map.get(key)
        if label and label not in found:
            found.append(label)

    return compact_series_label("RTX", found)


def build_label_from_tokens(vendor: str, token_groups: list[str]) -> str:
    tokens: list[str] = []
    for group in token_groups:
        for token in split_gpu_tokens(group):
            upper = token.upper()
            if upper not in tokens:
                tokens.append(upper)

    if not tokens:
        return ""

    if vendor == "INTEL":
        label = build_intel_label(tokens)
    elif vendor == "AMD":
        label = build_amd_label(tokens)
    elif vendor == "NVIDIA":
        label = build_nvidia_label(tokens)
    else:
        label = ""

    if label:
        return label

    print(f"[WARN] Could not convert gpu_model_match tokens for vendor={vendor}: {tokens}")
    return "Supported"


def build_vendor_display(
    game: dict[str, Any],
    sheet_vendor_rules: list[str],
    *,
    vendor: str,
    all_supported: bool,
) -> str:
    support_text = normalize_text(game.get(f"support_{vendor.lower()}"))
    if vendor == "intel" and support_text.lower() == "native xefg":
        return NATIVE_XEFG_TEXT

    vendor_key = vendor.upper()
    label = build_label_from_tokens(vendor_key, sheet_vendor_rules)
    has_install_profile_support = bool(all_supported or sheet_vendor_rules)

    if not has_install_profile_support:
        return "Not Supported"

    if label:
        return label

    if all_supported:
        return "Supported"

    return "Not Supported"


def build_games() -> list[dict[str, str]]:
    game_master = load_game_master()
    install_profile_rows = load_install_profile_rows()
    sheet_index, all_supported_games = build_sheet_index(install_profile_rows)

    unknown_sheet_games = sorted(game_id for game_id in sheet_index if game_id not in game_master)
    for game_id in unknown_sheet_games:
        print(f"[WARN] install_profile references unknown game_id in game_master.json: {game_id}")

    games: list[dict[str, str]] = []
    for game_id, game in game_master.items():
        vendor_rules = sheet_index.get(game_id, {"INTEL": [], "AMD": [], "NVIDIA": []})
        all_supported = game_id in all_supported_games
        intel = build_vendor_display(game, vendor_rules["INTEL"], vendor="intel", all_supported=all_supported)
        amd = build_vendor_display(game, vendor_rules["AMD"], vendor="amd", all_supported=all_supported)
        nvidia = build_vendor_display(game, vendor_rules["NVIDIA"], vendor="nvidia", all_supported=all_supported)

        if all(display == "Not Supported" for display in (intel, amd, nvidia)):
            continue

        games.append(
            {
                "game_name_kr": normalize_text(game.get("game_name_kr")),
                "game_name_en": normalize_text(game.get("game_name_en")),
                "Intel": intel,
                "AMD": amd,
                "NVIDIA": nvidia,
            }
        )

    games.sort(
        key=lambda item: (
            normalize_text(item.get("game_name_kr")).lower(),
            normalize_text(item.get("game_name_en")).lower(),
        )
    )
    return games


def build_markdown(games: list[dict[str, str]]) -> str:
    lines: list[str] = []
    lines.extend(WIKI_CAUTION_BLOCKS)
    lines.append("")
    lines.extend(RADEON_IGPU_NOTE_BLOCKS)
    lines.append("")
    lines.append(SUPPORTED_GAMES_TABLE_HEADER)
    lines.append(SUPPORTED_GAMES_TABLE_SEPARATOR)

    for game in games:
        lines.append(
            f"| {escape_md(game['game_name_kr'])} | "
            f"{escape_md(game['game_name_en'])} | "
            f"{escape_md(game['Intel'])} | "
            f"{escape_md(game['AMD'])} | "
            f"{escape_md(game['NVIDIA'])} |"
        )

    lines.append("")
    return "\n".join(lines)


def split_markdown_table_row(line: str) -> list[str]:
    text = normalize_text(line)
    if not text.startswith("|") or not text.endswith("|"):
        return []

    cells: list[str] = []
    current: list[str] = []
    escaped = False

    for char in text[1:-1]:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "|":
            cells.append(unescape_md_table_cell("".join(current)))
            current = []
            continue
        current.append(char)

    cells.append(unescape_md_table_cell("".join(current)))
    return cells


def make_game_identity(game: dict[str, str]) -> tuple[str, str]:
    return (
        normalize_text(game.get("game_name_kr")).lower(),
        normalize_text(game.get("game_name_en")).lower(),
    )


def make_markdown_game_identity(korean_title: str, english_title: str) -> tuple[str, str]:
    return (normalize_text(korean_title).lower(), normalize_text(english_title).lower())


def has_matching_game_identity(existing_game_keys: set[tuple[str, str]], game: dict[str, str]) -> bool:
    game_name_kr, game_name_en = make_game_identity(game)
    for existing_name_kr, existing_name_en in existing_game_keys:
        if game_name_en and existing_name_en == game_name_en:
            return True
        if game_name_kr and existing_name_kr == game_name_kr:
            return True
    return False


def find_matching_game(games: list[dict[str, str]], candidate: dict[str, str]) -> dict[str, str] | None:
    candidate_name_kr, candidate_name_en = make_game_identity(candidate)
    for game in games:
        game_name_kr, game_name_en = make_game_identity(game)
        if candidate_name_en and game_name_en == candidate_name_en:
            return game
        if candidate_name_kr and game_name_kr == candidate_name_kr:
            return game
    return None


def extract_supported_game_keys_from_markdown(markdown_text: str) -> set[tuple[str, str]]:
    lines = str(markdown_text or "").splitlines()
    header_index = None

    for index, line in enumerate(lines):
        if normalize_text(line) == SUPPORTED_GAMES_TABLE_HEADER:
            header_index = index
            break

    if header_index is None:
        return set()

    game_keys: set[tuple[str, str]] = set()
    for line in lines[header_index + 2:]:
        if not normalize_text(line).startswith("|"):
            break
        cells = split_markdown_table_row(line)
        if len(cells) < 2:
            continue
        game_keys.add(make_markdown_game_identity(cells[0], cells[1]))

    return game_keys


def extract_existing_new_games_block(markdown_text: str) -> str:
    lines = str(markdown_text or "").splitlines()
    start_index = None

    for index, line in enumerate(lines):
        if is_new_games_heading(line):
            start_index = index
            break

    if start_index is None:
        return ""

    end_index = len(lines)
    break_markers = {
        WIKI_CAUTION_BLOCKS[0],
        RADEON_IGPU_NOTE_BLOCKS[0],
    }
    seen_new_games_table_header = False
    for index in range(start_index + 1, len(lines)):
        stripped = normalize_text(lines[index])
        if stripped in break_markers:
            end_index = index
            break
        if stripped == NEW_GAMES_TABLE_HEADER:
            if seen_new_games_table_header:
                end_index = index
                break
            seen_new_games_table_header = True
            continue
        if stripped == LEGACY_NEW_GAMES_TABLE_HEADER and not seen_new_games_table_header:
            seen_new_games_table_header = True
            continue

    while end_index > start_index and not normalize_text(lines[end_index - 1]):
        end_index -= 1

    return "\n".join(lines[start_index:end_index])


def parse_iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(normalize_text(value))
    except ValueError:
        return None


def make_new_game_record(game: dict[str, str], detected_on: str) -> dict[str, str]:
    return {
        "game_name_kr": normalize_text(game.get("game_name_kr")),
        "game_name_en": normalize_text(game.get("game_name_en")),
        "Intel": normalize_text(game.get("Intel")),
        "AMD": normalize_text(game.get("AMD")),
        "NVIDIA": normalize_text(game.get("NVIDIA")),
        "detected_on": normalize_text(detected_on),
    }


def normalize_new_game_record(record: dict[str, str], fallback_detected_on: str) -> dict[str, str] | None:
    normalized = make_new_game_record(record, record.get("detected_on") or fallback_detected_on)
    if not normalized["game_name_kr"] and not normalized["game_name_en"]:
        return None
    if parse_iso_date(normalized["detected_on"]) is None:
        normalized["detected_on"] = fallback_detected_on
    return normalized


def extract_new_games_metadata_records(block_text: str, fallback_detected_on: str) -> list[dict[str, str]]:
    lines = str(block_text or "").splitlines()
    start_index = None

    for index, line in enumerate(lines):
        if normalize_text(line) == NEW_GAMES_METADATA_START:
            start_index = index + 1
            break

    if start_index is None:
        return []

    payload_lines: list[str] = []
    for line in lines[start_index:]:
        if normalize_text(line) == NEW_GAMES_METADATA_END:
            break
        payload_lines.append(line)
    else:
        return []

    try:
        payload = json.loads("\n".join(payload_lines))
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []

    records: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        record = normalize_new_game_record(item, fallback_detected_on)
        if record:
            records.append(record)
    return records


def extract_new_games_table_records(block_text: str, fallback_detected_on: str) -> list[dict[str, str]]:
    lines = str(block_text or "").splitlines()
    header_index = None

    for index, line in enumerate(lines):
        if normalize_text(line) in {NEW_GAMES_TABLE_HEADER, LEGACY_NEW_GAMES_TABLE_HEADER}:
            header_index = index
            break

    if header_index is None:
        return []

    records: list[dict[str, str]] = []
    for line in lines[header_index + 2:]:
        if not normalize_text(line).startswith("|"):
            break
        cells = split_markdown_table_row(line)
        if len(cells) < 2:
            continue
        record = normalize_new_game_record(
            {
                "game_name_kr": cells[0],
                "game_name_en": cells[1],
                "Intel": cells[2] if len(cells) > 2 else "",
                "AMD": cells[3] if len(cells) > 3 else "",
                "NVIDIA": cells[4] if len(cells) > 4 else "",
                "detected_on": fallback_detected_on,
            },
            fallback_detected_on,
        )
        if record:
            records.append(record)
    return records


def extract_existing_new_game_records(markdown_text: str, fallback_detected_on: str) -> list[dict[str, str]]:
    block_text = extract_existing_new_games_block(markdown_text)
    if not block_text:
        return []

    records = extract_new_games_metadata_records(block_text, fallback_detected_on)
    if records:
        return records
    return extract_new_games_table_records(block_text, fallback_detected_on)


def should_keep_new_game_record(record: dict[str, str], today: date, retention_days: int) -> bool:
    detected_on = parse_iso_date(record.get("detected_on"))
    if detected_on is None:
        detected_on = today
    cutoff = today - timedelta(days=retention_days)
    return detected_on >= cutoff


def add_new_game_record(records: list[dict[str, str]], record: dict[str, str]) -> None:
    if has_matching_game_identity({make_game_identity(item) for item in records}, record):
        return
    records.append(record)


def build_new_games_block(new_game_records: list[dict[str, str]]) -> str:
    if not new_game_records:
        return ""

    metadata = [
        {
            "game_name_kr": record["game_name_kr"],
            "game_name_en": record["game_name_en"],
            "Intel": record["Intel"],
            "AMD": record["AMD"],
            "NVIDIA": record["NVIDIA"],
            "detected_on": record["detected_on"],
        }
        for record in new_game_records
    ]

    lines = [
        NEW_GAMES_HEADING,
        "",
        NEW_GAMES_METADATA_START,
        json.dumps(metadata, ensure_ascii=False, indent=2),
        NEW_GAMES_METADATA_END,
        "",
        NEW_GAMES_TABLE_HEADER,
        NEW_GAMES_TABLE_SEPARATOR,
    ]

    for game in new_game_records:
        lines.append(
            f"| {escape_md(game['game_name_kr'])} | "
            f"{escape_md(game['game_name_en'])} | "
            f"{escape_md(game['Intel'])} | "
            f"{escape_md(game['AMD'])} | "
            f"{escape_md(game['NVIDIA'])} |"
        )

    return "\n".join(lines)


def apply_new_games_block(
    markdown_text: str,
    games: list[dict[str, str]],
    existing_markdown_text: str,
    *,
    retention_days: int,
) -> str:
    existing_game_keys = extract_supported_game_keys_from_markdown(existing_markdown_text)
    today = date.today()
    today_text = today.isoformat()
    new_game_records: list[dict[str, str]] = []

    for record in extract_existing_new_game_records(existing_markdown_text, today_text):
        if not should_keep_new_game_record(record, today, retention_days):
            continue
        current_game = find_matching_game(games, record)
        if current_game is None:
            continue
        add_new_game_record(new_game_records, make_new_game_record(current_game, record["detected_on"]))

    if existing_game_keys:
        for game in games:
            if not has_matching_game_identity(existing_game_keys, game):
                add_new_game_record(new_game_records, make_new_game_record(game, today_text))

    new_games_block = build_new_games_block(new_game_records)
    if not new_games_block:
        return markdown_text
    return f"{new_games_block}\n\n{markdown_text}"


def mask_sensitive_text(text: str) -> str:
    masked = str(text or "")
    if WIKI_PUSH_TOKEN:
        for secret in {WIKI_PUSH_TOKEN, quote(WIKI_PUSH_TOKEN, safe="")}:
            if secret:
                masked = masked.replace(secret, "***")
    return masked


def run_git(cmd: list[str], cwd: str | Path) -> None:
    display_cmd = mask_sensitive_text(" ".join(cmd))
    print(f"[RUN] {display_cmd}")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    stdout = mask_sensitive_text(result.stdout)
    stderr = mask_sensitive_text(result.stderr)

    if stdout:
        print("[STDOUT]")
        print(stdout)

    if stderr:
        print("[STDERR]")
        print(stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: {display_cmd}\n"
            f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        )


def update_wiki(games: list[dict[str, str]], markdown_text: str, *, retention_days: int) -> None:
    if not WIKI_PUSH_TOKEN:
        raise RuntimeError("Missing required environment variable: WIKI_PUSH_TOKEN")
    if not TARGET_WIKI_PAGE_FILE:
        raise RuntimeError("TARGET_WIKI_PAGE_FILE must not be empty.")

    authed_url = WIKI_REPO_URL.replace(
        "https://",
        f"https://x-access-token:{quote(WIKI_PUSH_TOKEN, safe='')}@",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "wiki_repo"
        run_git(["git", "clone", authed_url, str(repo_dir)], cwd=tmpdir)
        run_git(["git", "config", "user.name", "OptiScalerInstaller Bot"], cwd=repo_dir)
        run_git(["git", "config", "user.email", "actions@users.noreply.github.com"], cwd=repo_dir)

        target_file = repo_dir / TARGET_WIKI_PAGE_FILE
        baseline_file = repo_dir / (BASELINE_WIKI_PAGE_FILE or TARGET_WIKI_PAGE_FILE)
        existing_markdown_text = ""
        if baseline_file.exists():
            print(f"Using baseline wiki file: {baseline_file.name}")
            existing_markdown_text = baseline_file.read_text(encoding="utf-8")
        elif target_file.exists():
            print(f"Using target wiki file as baseline: {target_file.name}")
            existing_markdown_text = target_file.read_text(encoding="utf-8")

        final_markdown = apply_new_games_block(
            markdown_text,
            games,
            existing_markdown_text,
            retention_days=retention_days,
        )

        print("=== MARKDOWN PREVIEW START ===")
        print(final_markdown[:4000])
        print("=== MARKDOWN PREVIEW END ===")

        print(f"Writing wiki file: {target_file}")
        target_file.write_text(final_markdown, encoding="utf-8")

        run_git(["git", "add", TARGET_WIKI_PAGE_FILE], cwd=repo_dir)

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        print("[GIT STATUS PORCELAIN]")
        print(status.stdout)

        if not status.stdout.strip():
            print("No changes detected.")
            return

        run_git(["git", "commit", "-m", "Auto update supported game list"], cwd=repo_dir)
        try:
            run_git(["git", "push"], cwd=repo_dir)
        except RuntimeError as exc:
            message = str(exc)
            if "Permission to" in message and "403" in message:
                raise RuntimeError(
                    "GitHub rejected the wiki push with HTTP 403. The token authenticated, but it does not have "
                    "write access to the wiki repository. Use a token with wiki write access."
                ) from exc
            raise

        print("Wiki updated successfully.")


def main() -> None:
    retention_days = require_int_env_value("NEW_GAMES_RETENTION_DAYS", 30)
    games = build_games()
    markdown = build_markdown(games)
    update_wiki(games, markdown, retention_days=retention_days)


if __name__ == "__main__":
    main()
