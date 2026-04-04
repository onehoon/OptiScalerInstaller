from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import customtkinter as ctk


@dataclass(frozen=True)
class MainUiTheme:
    panel_color: str
    surface_color: str
    title_text_color: str
    font_heading: str
    font_ui: str
    status_indicator_size: int
    status_indicator_loading_color: str
    status_indicator_y_offset: int
    status_text_color: str
    content_side_pad: int
    browse_button_color: str
    browse_button_hover_color: str
    scan_status_text_color: str
    scan_meta_right_inset: int
    supported_games_wiki_url: str
    link_active_color: str
    meta_right_pad: int
    selected_game_highlight_color: str
    grid_width: int
    grid_height: int
    install_button_disabled_color: str
    install_button_text_color: str
    install_button_border_disabled_color: str


def build_main_ui(app: Any, theme: MainUiTheme) -> None:
    app.root.configure(fg_color=theme.panel_color)
    app.root.grid_rowconfigure(2, weight=1)
    app.root.grid_columnconfigure(0, weight=1)

    _build_header(app, theme)
    _build_scan_row(app, theme)
    _build_grid_area(app, theme)
    _build_bottom_bar(app, theme)


def _build_header(app: Any, theme: MainUiTheme) -> None:
    hdr = ctk.CTkFrame(app.root, fg_color=theme.panel_color, corner_radius=0)
    hdr.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
    hdr.grid_columnconfigure(0, weight=1)

    title_lbl = ctk.CTkLabel(
        hdr,
        text=app.txt.main.app_title,
        font=ctk.CTkFont(family=theme.font_heading, size=20, weight="bold"),
        text_color=theme.title_text_color,
    )
    title_lbl.grid(row=0, column=0, padx=24, pady=(18, 2), sticky="w")

    sub_frame = ctk.CTkFrame(hdr, fg_color=theme.panel_color, corner_radius=0)
    sub_frame.grid(row=1, column=0, padx=24, pady=(0, 14), sticky="ew")
    sub_frame.grid_columnconfigure(0, weight=1)

    app.gpu_lbl = ctk.CTkLabel(
        sub_frame,
        text=app._format_gpu_label_text(app.gpu_info),
        font=ctk.CTkFont(family=theme.font_ui, size=11),
        text_color="#C5CFDB",
        anchor="w",
    )
    app.gpu_lbl.grid(row=0, column=0, padx=(1, 0), sticky="w")

    app.status_badge = ctk.CTkFrame(
        sub_frame,
        fg_color="transparent",
        corner_radius=0,
    )
    app.status_badge.grid(row=0, column=1, sticky="e", padx=(8, 0))
    app.status_badge.grid_columnconfigure(1, weight=1)

    app.status_badge_dot = ctk.CTkFrame(
        app.status_badge,
        width=theme.status_indicator_size,
        height=theme.status_indicator_size,
        fg_color=theme.status_indicator_loading_color,
        corner_radius=theme.status_indicator_size // 2,
    )
    app.status_badge_dot.grid(
        row=0,
        column=0,
        padx=(0, 8),
        pady=(theme.status_indicator_y_offset, 0),
        sticky="w",
    )

    app.status_badge_label = ctk.CTkLabel(
        app.status_badge,
        text=app.txt.main.status_game_db,
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
        text_color=theme.status_text_color,
        anchor="w",
    )
    app.status_badge_label.grid(row=0, column=1, sticky="w")
    app._set_status_badge_state(app.txt.main.status_game_db, theme.status_indicator_loading_color, pulse=True)

    sep = ctk.CTkFrame(hdr, height=1, fg_color="#4A5361", corner_radius=0)
    sep.grid(row=2, column=0, sticky="ew")


def _build_scan_row(app: Any, theme: MainUiTheme) -> None:
    row = ctk.CTkFrame(app.root, fg_color=theme.surface_color, corner_radius=0)
    row.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
    row.grid_columnconfigure(2, weight=1)
    app.scan_row = row

    sec_lbl = ctk.CTkLabel(
        row,
        text=app.txt.main.scan_section_title,
        font=ctk.CTkFont(family=theme.font_heading, size=12, weight="bold"),
        text_color="#F1F5F9",
    )
    sec_lbl.grid(row=0, column=0, padx=(theme.content_side_pad, 10), pady=(8, 8), sticky="w")

    app.btn_select_folder = ctk.CTkButton(
        row,
        text=app.txt.main.browse_button,
        width=110,
        height=32,
        corner_radius=8,
        fg_color=theme.browse_button_color,
        hover_color=theme.browse_button_hover_color,
        text_color="#F1F5F9",
        font=ctk.CTkFont(family=theme.font_ui, size=11, weight="bold"),
        command=app.select_game_folder,
    )
    app.btn_select_folder.grid(row=0, column=1, padx=4, pady=(8, 8), sticky="w")

    app.lbl_scan_status = ctk.CTkLabel(
        row,
        text="",
        font=ctk.CTkFont(family=theme.font_ui, size=11),
        text_color=theme.scan_status_text_color,
        anchor="w",
        justify="left",
    )
    app.lbl_scan_status.grid(
        row=1,
        column=0,
        columnspan=4,
        padx=(theme.content_side_pad, theme.scan_meta_right_inset),
        pady=(0, 10),
        sticky="w",
    )
    app.lbl_scan_status.grid_remove()


def _build_grid_area(app: Any, theme: MainUiTheme) -> None:
    wrapper = ctk.CTkFrame(app.root, fg_color=theme.panel_color, corner_radius=0)
    wrapper.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
    wrapper.grid_rowconfigure(1, weight=1)
    wrapper.grid_columnconfigure(0, weight=1)

    header_row = ctk.CTkFrame(wrapper, fg_color="transparent", corner_radius=0)
    header_row.grid(row=0, column=0, padx=(theme.content_side_pad, theme.content_side_pad), pady=(6, 6), sticky="ew")
    header_row.grid_columnconfigure(1, weight=1)

    app.lbl_supported_games_wiki_link = ctk.CTkLabel(
        header_row,
        text=app.txt.main.supported_games_link,
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold", underline=True),
        text_color=theme.link_active_color if theme.supported_games_wiki_url else theme.status_text_color,
        anchor="w",
        justify="left",
        cursor="hand2" if theme.supported_games_wiki_url else "arrow",
    )
    app.lbl_supported_games_wiki_link.grid(row=0, column=0, padx=(14, 12), pady=(1, 0), sticky="w")
    if theme.supported_games_wiki_url:
        app.lbl_supported_games_wiki_link.bind("<Enter>", lambda _event: app._set_supported_games_wiki_link_hover(True))
        app.lbl_supported_games_wiki_link.bind("<Leave>", lambda _event: app._set_supported_games_wiki_link_hover(False))
        app.lbl_supported_games_wiki_link.bind("<Button-1>", app._open_supported_games_wiki)

    selected_header_row = ctk.CTkFrame(header_row, fg_color="transparent", corner_radius=0)
    selected_header_row.grid(row=0, column=1, padx=(8, theme.meta_right_pad), pady=(1, 0), sticky="ew")
    selected_header_row.grid_columnconfigure(0, weight=1)

    app.lbl_selected_game_header = ctk.CTkLabel(
        selected_header_row,
        text=app._get_selected_game_header_text(),
        font=ctk.CTkFont(family=theme.font_ui, size=12, weight="bold"),
        text_color=theme.selected_game_highlight_color,
        anchor="e",
        justify="right",
    )
    app.lbl_selected_game_header.grid(row=0, column=1, sticky="e")

    app.games_scroll = ctk.CTkScrollableFrame(
        wrapper,
        width=theme.grid_width,
        height=theme.grid_height,
        fg_color=theme.panel_color,
        scrollbar_button_color="#566171",
        scrollbar_button_hover_color="#6A7587",
        corner_radius=0,
    )
    app.games_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 8))
    app._configure_card_columns(app._grid_cols_current)
    app.games_scroll.bind("<Configure>", app._on_games_area_resize)
    try:
        canvas = getattr(app.games_scroll, "_parent_canvas", None)
        scrollbar = getattr(app.games_scroll, "_scrollbar", None)
        if canvas is not None:
            canvas.bind("<MouseWheel>", app._on_games_scroll, add="+")
            canvas.bind("<Button-4>", app._on_games_scroll, add="+")
            canvas.bind("<Button-5>", app._on_games_scroll, add="+")
            canvas.bind("<ButtonRelease-1>", app._on_games_scroll, add="+")
            canvas.bind("<Configure>", app._on_games_area_resize, add="+")
        if canvas is not None and scrollbar is not None:
            scrollbar.configure(command=app._on_games_scrollbar_command)
    except Exception:
        logging.debug("Failed to bind scroll events for image priority updates")

    app.empty_label = ctk.CTkLabel(
        app.games_scroll,
        text="",
        font=ctk.CTkFont(family=theme.font_ui, size=13),
        text_color="#9AA8BC",
    )


def _build_bottom_bar(app: Any, theme: MainUiTheme) -> None:
    bar = ctk.CTkFrame(app.root, fg_color=theme.surface_color, corner_radius=0, height=142)
    bar.grid(row=3, column=0, sticky="ew", padx=0, pady=0)
    bar.grid_propagate(False)
    bar.grid_columnconfigure(0, weight=1)

    title_line = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=0)
    title_line.grid(row=0, column=0, padx=20, pady=(7, 2), sticky="ew")
    title_line.grid_columnconfigure(1, weight=1)

    sec_lbl = ctk.CTkLabel(
        title_line,
        text=app.txt.main.install_section_title,
        font=ctk.CTkFont(family=theme.font_heading, size=12, weight="bold"),
        text_color="#F1F5F9",
    )
    sec_lbl.grid(row=0, column=0, sticky="w")

    app.lbl_optiscaler_version_line = ctk.CTkLabel(
        title_line,
        text="",
        font=ctk.CTkFont(family=theme.font_ui, size=11),
        text_color="#AEB9C8",
        anchor="e",
        justify="right",
        wraplength=520,
    )
    app.lbl_optiscaler_version_line.grid(row=0, column=1, padx=(10, 0), pady=(2, 0), sticky="e")

    mid_bottom = ctk.CTkFrame(bar, fg_color=theme.surface_color, corner_radius=0)
    mid_bottom.grid(row=1, column=0, sticky="ew", padx=20, pady=(2, 0))
    mid_bottom.grid_columnconfigure(0, weight=1)

    app.apply_btn = ctk.CTkButton(
        mid_bottom,
        text=app.txt.main.install_button,
        width=104,
        height=87,
        corner_radius=10,
        fg_color=theme.install_button_disabled_color,
        hover_color=theme.install_button_disabled_color,
        text_color=theme.install_button_text_color,
        border_width=1,
        border_color=theme.install_button_border_disabled_color,
        font=ctk.CTkFont(family=theme.font_ui, size=14, weight="bold"),
        state="disabled",
        command=app.apply_optiscaler,
    )
    app.apply_btn.grid(row=0, column=1, padx=(10, 0), pady=(0, 0))

    app.info_text = ctk.CTkTextbox(
        mid_bottom,
        height=87,
        corner_radius=8,
        fg_color="#2A303A",
        text_color="#E3EAF3",
        font=ctk.CTkFont(family=theme.font_ui, size=12),
        state="disabled",
        wrap="word",
        border_width=0,
    )
    app.info_text.grid(row=0, column=0, sticky="ew", pady=(0, 0))
    app._apply_information_text_shift()

    app._refresh_optiscaler_archive_info_ui()
    app._set_information_text(app.txt.main.select_game_hint)
    app._update_install_button_state()
