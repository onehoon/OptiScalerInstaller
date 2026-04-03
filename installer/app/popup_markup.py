from __future__ import annotations

from dataclasses import dataclass
import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Iterator, Optional


_OPEN_MARKUP_PATTERN = re.compile(r"\[\s*RED\s*\]", re.IGNORECASE)
_CLOSE_MARKUP_PATTERN = re.compile(r"\[\s*END\s*[\]\}]", re.IGNORECASE)
_PARAGRAPH_MARKUP_PATTERN = re.compile(r"\[\s*P\s*\]", re.IGNORECASE)
_LINE_BREAK_MARKUP_PATTERN = re.compile(r"\[\s*BR\s*\]", re.IGNORECASE)
_DOT_MARKUP_PATTERN = re.compile(r"\[\s*DOT\s*\][ \t]*", re.IGNORECASE)
_INDENT_MARKUP_PATTERN = re.compile(r"\[\s*INDENT\s*\]", re.IGNORECASE)
_INDENT_SPACES = 3


@dataclass(frozen=True)
class MarkupRenderResult:
    plain_text: str
    base_font: tkfont.Font
    emphasis_font: tkfont.Font


@dataclass(frozen=True)
class PopupMarkupText:
    widget: tk.Text
    plain_text: str
    base_font: tkfont.Font
    emphasis_font: tkfont.Font


def normalize_popup_markup_text(raw_text: str) -> str:
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _PARAGRAPH_MARKUP_PATTERN.sub("\n\n", text)
    text = _LINE_BREAK_MARKUP_PATTERN.sub("\n", text)
    text = _DOT_MARKUP_PATTERN.sub("\u2022 ", text)
    # Keep indent width easy to tune in code while the sheet syntax stays stable.
    text = _INDENT_MARKUP_PATTERN.sub(" " * _INDENT_SPACES, text)
    return text


def _iter_markup_segments(raw_text: str) -> Iterator[tuple[str, bool]]:
    text = normalize_popup_markup_text(raw_text)
    cursor = 0

    while cursor < len(text):
        open_match = _OPEN_MARKUP_PATTERN.search(text, cursor)
        if open_match is None:
            tail = text[cursor:]
            if tail:
                yield tail, False
            return

        if open_match.start() > cursor:
            yield text[cursor:open_match.start()], False

        close_match = _CLOSE_MARKUP_PATTERN.search(text, open_match.end())
        if close_match is None:
            emphasized_tail = text[open_match.end():]
            if emphasized_tail:
                yield emphasized_tail, True
            return

        emphasized_text = text[open_match.end():close_match.start()]
        if emphasized_text:
            yield emphasized_text, True
        cursor = close_match.end()


def strip_markup_text(raw_text: str) -> str:
    return "".join(segment for segment, _ in _iter_markup_segments(raw_text))


def estimate_wrapped_text_lines(text: str, font: tkfont.Font, max_width_px: int) -> int:
    normalized = normalize_popup_markup_text(text)
    available_width = max(32, int(max_width_px or 0))
    total_lines = 0

    for paragraph in normalized.split("\n"):
        if paragraph == "":
            total_lines += 1
            continue

        remaining = paragraph
        while remaining:
            if font.measure(remaining) <= available_width:
                total_lines += 1
                break

            fit_len = 0
            lo, hi = 1, len(remaining)
            while lo <= hi:
                mid = (lo + hi) // 2
                if font.measure(remaining[:mid]) <= available_width:
                    fit_len = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

            if fit_len <= 0:
                fit_len = 1

            break_at = fit_len
            for idx in range(fit_len - 1, 0, -1):
                if remaining[idx].isspace():
                    break_at = idx
                    break

            if break_at <= 0:
                break_at = fit_len

            total_lines += 1
            remaining = remaining[break_at:].lstrip()

    return max(1, total_lines)


def _clone_font(
    font_source: object,
    *,
    family: Optional[str] = None,
    size: Optional[int] = None,
    weight: Optional[str] = None,
) -> tkfont.Font:
    font_spec = font_source
    if hasattr(font_source, "cget"):
        try:
            font_spec = font_source.cget("font")
        except Exception:
            font_spec = None

    if isinstance(font_spec, tkfont.Font):
        font = tkfont.Font(font=font_spec)
    else:
        try:
            font = tkfont.Font(font=tkfont.nametofont(font_spec))
        except Exception:
            try:
                font = tkfont.Font(font=font_spec)
            except Exception:
                font = tkfont.Font()

    config = {}
    if family is not None:
        config["family"] = family
    if size is not None:
        config["size"] = size
    if weight is not None:
        config["weight"] = weight
    if config:
        font.configure(**config)
    return font


def render_markup_to_text_widget(
    text_widget: tk.Text,
    raw_text: str,
    *,
    emphasis_tag: str = "popup_red_emphasis",
    emphasis_color: str = "#FF4D4F",
    base_font_source: object = None,
    base_font_family: Optional[str] = None,
    base_font_size: Optional[int] = None,
    base_font_weight: Optional[str] = None,
    emphasis_font_size: Optional[int] = None,
    emphasis_weight: str = "bold",
    emphasis_size_offset: int = 1,
    clear: bool = True,
    trim_emphasis: bool = False,
) -> MarkupRenderResult:
    base_font = _clone_font(
        text_widget if base_font_source is None else base_font_source,
        family=base_font_family,
        size=base_font_size,
        weight=base_font_weight,
    )
    text_widget.configure(font=base_font)

    emphasis_font = tkfont.Font(font=base_font)
    resolved_base_size = int(base_font.cget("size") or 12)
    if emphasis_font_size is None:
        emphasis_font_size = (
            resolved_base_size + emphasis_size_offset
            if resolved_base_size >= 0
            else resolved_base_size - emphasis_size_offset
        )
    emphasis_font.configure(size=emphasis_font_size, weight=emphasis_weight)
    text_widget.tag_configure(emphasis_tag, foreground=emphasis_color, font=emphasis_font)
    setattr(text_widget, "_popup_markup_base_font", base_font)
    setattr(text_widget, "_popup_markup_emphasis_font", emphasis_font)

    original_state = None
    try:
        original_state = str(text_widget.cget("state"))
    except Exception:
        original_state = None

    if original_state and original_state != "normal":
        text_widget.configure(state="normal")

    if clear:
        text_widget.delete("1.0", "end")

    plain_segments: list[str] = []
    for segment, is_emphasis in _iter_markup_segments(raw_text):
        if is_emphasis and trim_emphasis:
            segment = segment.strip()
        if not segment:
            continue
        if is_emphasis:
            text_widget.insert("end", segment, (emphasis_tag,))
        else:
            text_widget.insert("end", segment)
        plain_segments.append(segment)

    if original_state and original_state != "normal":
        text_widget.configure(state=original_state)

    return MarkupRenderResult(
        plain_text="".join(plain_segments),
        base_font=base_font,
        emphasis_font=emphasis_font,
    )


def create_popup_markup_text(
    parent,
    raw_text: str,
    *,
    background_color: str,
    body_text_color: str,
    font_family: str,
    width: int = 58,
    height: int = 1,
    base_font_size: int = 13,
    emphasis_color: str = "#FF4D4F",
    emphasis_font_size: Optional[int] = None,
    emphasis_weight: str = "bold",
    emphasis_size_offset: int = 1,
    wrap: str = "word",
    padx: int = 0,
    pady: int = 0,
    spacing1: int = 0,
    spacing2: int = 0,
    spacing3: int = 0,
) -> PopupMarkupText:
    text_widget = tk.Text(
        parent,
        wrap=wrap,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        bg=background_color,
        fg=body_text_color,
        width=width,
        height=height,
        insertwidth=0,
        takefocus=0,
        cursor="arrow",
    )
    text_widget.configure(
        padx=padx,
        pady=pady,
        spacing1=spacing1,
        spacing2=spacing2,
        spacing3=spacing3,
    )
    result = render_markup_to_text_widget(
        text_widget,
        raw_text,
        base_font_source=text_widget,
        base_font_family=font_family,
        base_font_size=base_font_size,
        emphasis_color=emphasis_color,
        emphasis_font_size=emphasis_font_size,
        emphasis_weight=emphasis_weight,
        emphasis_size_offset=emphasis_size_offset,
    )
    return PopupMarkupText(
        widget=text_widget,
        plain_text=result.plain_text,
        base_font=result.base_font,
        emphasis_font=result.emphasis_font,
    )
