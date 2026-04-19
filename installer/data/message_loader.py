from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from installer.common.network_utils import get_shared_retry_session


_file_session = get_shared_retry_session()


@dataclass(frozen=True)
class MessageTemplate:
    message_id: str
    category: str
    ko: str
    en: str
    url: str
    memo: str = ""


@dataclass(frozen=True)
class MessageBinding:
    game_id: str
    gpu_vendor: str
    stage: str
    message_id: str
    priority: int
    memo: str = ""


@dataclass(frozen=True)
class MessageRepository:
    templates: dict[str, MessageTemplate]
    bindings: tuple[MessageBinding, ...]


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_key(value: object) -> str:
    return _normalize_text(value).casefold()


def _normalize_lang(lang: str) -> str:
    return "ko" if str(lang or "").lower().startswith("ko") else "en"


def _parse_priority(value: object, default: int = 100) -> int:
    try:
        return int(_normalize_text(value) or default)
    except (TypeError, ValueError):
        return default


def _fetch_remote_text(url: str, *, timeout_seconds: float = 10.0) -> str:
    normalized = _normalize_text(url)
    if not normalized:
        raise ValueError("Remote JSON URL is empty")
    response = _file_session.get(normalized, timeout=timeout_seconds)
    response.raise_for_status()
    return response.content.decode("utf-8-sig")


def _parse_message_center_rows(rows: list[dict[str, Any]]) -> dict[str, MessageTemplate]:
    result: dict[str, MessageTemplate] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_id = _normalize_text(row.get("message_id"))
        if not message_id:
            continue
        result[message_id] = MessageTemplate(
            message_id=message_id,
            category=_normalize_text(row.get("category")).casefold(),
            ko=_normalize_text(row.get("ko")),
            en=_normalize_text(row.get("en")),
            url=_normalize_text(row.get("url")),
            memo=_normalize_text(row.get("memo")),
        )
    return result


def _parse_message_binding_rows(rows: list[dict[str, Any]]) -> tuple[MessageBinding, ...]:
    result: list[MessageBinding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_id = _normalize_text(row.get("message_id"))
        stage = _normalize_text(row.get("stage"))
        if not message_id or not stage:
            continue
        result.append(
            MessageBinding(
                game_id=_normalize_text(row.get("game_id")) or "*",
                gpu_vendor=_normalize_text(row.get("gpu_vendor")) or "all",
                stage=stage.casefold(),
                message_id=message_id,
                priority=_parse_priority(row.get("priority"), 100),
                memo=_normalize_text(row.get("memo")),
            )
        )
    return tuple(result)


def _parse_rows_from_text(text: str) -> list[dict[str, Any]]:
    normalized = str(text or "").lstrip("\ufeff").strip()
    if not normalized:
        return []
    payload = json.loads(normalized)
    if not isinstance(payload, list):
        raise ValueError("Message payload JSON must be a list")
    return [row for row in payload if isinstance(row, dict)]


def load_message_center(source_url: str = "", *, timeout_seconds: float = 10.0) -> dict[str, MessageTemplate]:
    text = _fetch_remote_text(source_url, timeout_seconds=timeout_seconds)
    return _parse_message_center_rows(_parse_rows_from_text(text))


def load_message_binding(source_url: str = "", *, timeout_seconds: float = 10.0) -> tuple[MessageBinding, ...]:
    text = _fetch_remote_text(source_url, timeout_seconds=timeout_seconds)
    return _parse_message_binding_rows(_parse_rows_from_text(text))


def build_message_repository(
    templates: dict[str, MessageTemplate],
    bindings: tuple[MessageBinding, ...] | list[MessageBinding],
) -> MessageRepository:
    return MessageRepository(dict(templates or {}), tuple(bindings or ()))


def _binding_matches(binding: MessageBinding, *, stage: str, game_id: str, gpu_vendor: str) -> bool:
    if _normalize_key(binding.stage) != _normalize_key(stage):
        return False

    binding_game = _normalize_key(binding.game_id)
    target_game = _normalize_key(game_id)
    if binding_game not in {"*", target_game}:
        return False

    binding_vendor = _normalize_key(binding.gpu_vendor)
    target_vendor = _normalize_key(gpu_vendor) or "default"
    if binding_vendor not in {"all", target_vendor}:
        return False
    return True


def _binding_sort_key(binding: MessageBinding, *, game_id: str, gpu_vendor: str) -> tuple[int, int, int]:
    game_exact_rank = 0 if _normalize_key(binding.game_id) == _normalize_key(game_id) else 1
    vendor_exact_rank = 0 if _normalize_key(binding.gpu_vendor) == _normalize_key(gpu_vendor) else 1
    return (game_exact_rank, vendor_exact_rank, int(binding.priority))


def _resolve_template_text(template: MessageTemplate, *, lang: str) -> str:
    normalized_lang = _normalize_lang(lang)
    return template.ko if normalized_lang == "ko" else template.en


def resolve_stage_popup_text(
    repo: MessageRepository,
    *,
    stage: str,
    game_id: str,
    gpu_vendor: str,
    lang: str,
) -> str:
    matches = [
        binding
        for binding in repo.bindings
        if _binding_matches(binding, stage=stage, game_id=game_id, gpu_vendor=gpu_vendor)
    ]
    matches.sort(key=lambda item: _binding_sort_key(item, game_id=game_id, gpu_vendor=gpu_vendor))

    seen: set[str] = set()
    text_parts: list[str] = []
    for binding in matches:
        template = repo.templates.get(binding.message_id)
        if template is None or template.category != "popup":
            continue
        text = _resolve_template_text(template, lang=lang).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        text_parts.append(text)
    return "[P]".join(text_parts)


def resolve_stage_guide_url(repo: MessageRepository, *, game_id: str, gpu_vendor: str) -> str:
    matches = [
        binding
        for binding in repo.bindings
        if _binding_matches(binding, stage="guide", game_id=game_id, gpu_vendor=gpu_vendor)
    ]
    matches.sort(key=lambda item: _binding_sort_key(item, game_id=game_id, gpu_vendor=gpu_vendor))
    for binding in matches:
        template = repo.templates.get(binding.message_id)
        if template is None or template.category != "guide_url":
            continue
        url = template.url.strip()
        if url:
            return url
    return ""


def resolve_startup_warning_text(repo: MessageRepository, *, gpu_vendor: str, lang: str) -> str:
    return resolve_stage_popup_text(repo, stage="startup", game_id="*", gpu_vendor=gpu_vendor, lang=lang)


def materialize_bound_messages_into_game_db(
    game_db: dict[str, dict[str, Any]],
    repo: MessageRepository,
    *,
    gpu_vendor: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for match_key, raw_game in dict(game_db or {}).items():
        game = dict(raw_game or {})
        game_id = _normalize_text(game.get("game_id"))
        if game_id:
            popup_ko = resolve_stage_popup_text(repo, stage="install_pre", game_id=game_id, gpu_vendor=gpu_vendor, lang="ko")
            popup_en = resolve_stage_popup_text(repo, stage="install_pre", game_id=game_id, gpu_vendor=gpu_vendor, lang="en")
            after_popup_ko = resolve_stage_popup_text(repo, stage="install_post", game_id=game_id, gpu_vendor=gpu_vendor, lang="ko")
            after_popup_en = resolve_stage_popup_text(repo, stage="install_post", game_id=game_id, gpu_vendor=gpu_vendor, lang="en")
            guide_url = resolve_stage_guide_url(repo, game_id=game_id, gpu_vendor=gpu_vendor)

            if popup_ko:
                game["__install_pre_kr__"] = popup_ko
            if popup_en:
                game["__install_pre_en__"] = popup_en
            if after_popup_ko:
                game["__install_post_kr__"] = after_popup_ko
            if after_popup_en:
                game["__install_post_en__"] = after_popup_en
            if guide_url:
                game["__guide_url__"] = guide_url

        result[match_key] = game
    return result


__all__ = [
    "MessageBinding",
    "MessageRepository",
    "MessageTemplate",
    "build_message_repository",
    "load_message_binding",
    "load_message_center",
    "materialize_bound_messages_into_game_db",
    "resolve_stage_guide_url",
    "resolve_stage_popup_text",
    "resolve_startup_warning_text",
]
