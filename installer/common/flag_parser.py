from __future__ import annotations

from collections.abc import Iterable


_TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "n", "off", "null", "none", "na", "n/a", "-"})


def _normalize_tokens(tokens: Iterable[object]) -> set[str]:
    normalized: set[str] = set()
    for token in tokens:
        text = str(token or "").strip().lower()
        if text:
            normalized.add(text)
    return normalized


def parse_bool_token(
    value: object,
    *,
    empty_default: bool = False,
    unknown_default: bool = False,
    extra_false_tokens: Iterable[object] = (),
    extra_true_tokens: Iterable[object] = (),
) -> bool:
    if isinstance(value, bool):
        return value

    text = str(value or "").strip().lower()
    if not text:
        return bool(empty_default)

    false_tokens = _FALSE_TOKENS | _normalize_tokens(extra_false_tokens)
    if text in false_tokens:
        return False

    true_tokens = _TRUE_TOKENS | _normalize_tokens(extra_true_tokens)
    if text in true_tokens:
        return True

    return bool(unknown_default)


__all__ = ["parse_bool_token"]
