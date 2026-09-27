"""Shared visual theme for the app — a single place to tweak colors/fonts
so every tab looks consistent."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---- palette ----
BG = "#F4F5F7"            # app background
PANEL_BG = "#FFFFFF"      # card/panel background
BORDER = "#E1E3E8"
TEXT = "#1F2430"
TEXT_MUTED = "#6B7280"
ACCENT = "#3B6E4E"        # deep green, chess-board-adjacent
ACCENT_HOVER = "#2F5A3F"
ACCENT_TEXT = "#FFFFFF"
SUCCESS = "#2E7D32"
ERROR = "#C0392B"

# board colors (kept here so drill/puzzle boards and the theme agree)
# warm wood-tone palette (cream/brown), not flat green
BOARD_LIGHT = "#F0D9B5"
BOARD_DARK = "#B5885A"
BOARD_HIGHLIGHT = "#AFA23A"   # olive, for the selected square
# from/to squares of the last move played, tinted per square color
LAST_MOVE_LIGHT = "#F2E27A"
LAST_MOVE_DARK = "#C9AE4E"

# piece fill colors — cream for white pieces, dark brown for black, each
# with a contrasting outline so they read clearly against either square
PIECE_WHITE_FILL = "#FDF6EC"
PIECE_WHITE_OUTLINE = "#3B2314"
PIECE_BLACK_FILL = "#3B2314"
PIECE_BLACK_OUTLINE = "#1C1108"

FONT_FAMILY = "Segoe UI"  # falls back gracefully on non-Windows
FONT_BASE = (FONT_FAMILY, 11)
FONT_BOLD = (FONT_FAMILY, 11, "bold")
FONT_HEADING = (FONT_FAMILY, 15, "bold")
FONT_STATUS = (FONT_FAMILY, 13)
FONT_SMALL = (FONT_FAMILY, 9)


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)

    style = ttk.Style(root)
    # 'clam' is the most stylable built-in theme across platforms
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, font=FONT_BASE)

    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL_BG)

    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_BASE)
    style.configure("Panel.TLabel", background=PANEL_BG, foreground=TEXT, font=FONT_BASE)
    style.configure("Heading.TLabel", background=BG, foreground=TEXT, font=FONT_HEADING)
    style.configure("Status.TLabel", background=BG, foreground=TEXT, font=FONT_STATUS)
    style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED, font=FONT_SMALL)
    style.configure("Section.TLabel", background=BG, foreground=TEXT, font=FONT_BOLD)

    # same roles, for labels sitting on a white panel/card
    style.configure("PanelHeading.TLabel", background=PANEL_BG, foreground=TEXT, font=FONT_HEADING)
    style.configure("PanelStatus.TLabel", background=PANEL_BG, foreground=TEXT, font=FONT_STATUS)
    style.configure("PanelMuted.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=FONT_SMALL)

    style.configure("TRadiobutton", background=BG, foreground=TEXT, font=FONT_BASE)
    style.configure("TSpinbox", fieldbackground=PANEL_BG, padding=4)

    style.configure(
        "Accent.TButton",
        background=ACCENT, foreground=ACCENT_TEXT, font=FONT_BOLD,
        padding=(14, 10), borderwidth=0, focusthickness=0,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", "#A9BCAF"), ("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER)],
        foreground=[("disabled", "#EEF2EF")],
    )

    style.configure(
        "Secondary.TButton",
        background=PANEL_BG, foreground=TEXT, font=FONT_BASE,
        padding=(14, 10), borderwidth=1, relief="solid",
        bordercolor=BORDER, focusthickness=0,
    )
    style.map(
        "Secondary.TButton",
        background=[("active", BG), ("pressed", BG)],
        foreground=[("disabled", "#A0A6B1")],
    )

    style.configure(
        "TNotebook", background=BG, borderwidth=0, tabmargins=(8, 8, 8, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=BG, foreground=TEXT_MUTED, font=FONT_BOLD,
        padding=(18, 10), borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL_BG)],
        foreground=[("selected", TEXT)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=PANEL_BG, background=PANEL_BG, foreground=TEXT,
        padding=8,
    )


def panel(master, **kwargs) -> ttk.Frame:
    """A white 'card' frame with a subtle border, used to group controls."""
    outer = tk.Frame(master, bg=BORDER, **kwargs)
    inner = ttk.Frame(outer, style="Panel.TFrame")
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    return inner
