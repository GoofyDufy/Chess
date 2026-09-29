"""Shared visual theme — the "Midnight study" look from the Claude Design
mockup: dark toned neutrals, one warm amber accent, rounded cards
(CustomTkinter). A single place to tweak colors/fonts so every screen
matches. Tkinter-native widgets that CustomTkinter lacks (Listbox,
Treeview, the board Canvas) are styled to match here too."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from paths import resource_dir

# ---- palette ----
BG = "#111317"            # app background
SIDEBAR_BG = "#16181D"
PANEL_BG = "#1A1D23"      # card background
PANEL_ALT = "#20242B"     # tiles / rows inside a card
CONTROL_BG = "#2A2E37"    # selected nav item, track of bars, hover
INPUT_BG = "#111317"
BORDER = "#262A32"
BORDER_STRONG = "#333844"
TEXT = "#ECE8E1"
TEXT_MUTED = "#9AA1AD"
TEXT_SOFT = "#A9AFBA"
ACCENT = "#E6A94A"        # warm amber
ACCENT_HOVER = "#F0BC68"
ACCENT_TEXT = "#1A1206"   # text on the accent
SUCCESS = "#7DCB98"
ERROR = "#E8735E"
WARNING = "#E6A94A"
INFO = "#8FB3E8"

# board colors (kept here so drill/puzzle boards and the theme agree)
# warm wood-tone palette (cream/brown), not flat green
BOARD_LIGHT = "#F0D9B5"
BOARD_DARK = "#B58863"
BOARD_HIGHLIGHT = "#AFA23A"   # olive, for the selected square
# from/to squares of the last move played, tinted per square color
LAST_MOVE_LIGHT = "#F3E27C"
LAST_MOVE_DARK = "#CDB04F"

# piece colors: the "Cool slate" set (see piece_shapes.py) - light slate
# pieces with a navy outline, navy pieces with a near-black outline
PIECE_WHITE_FILL = "#EEF3F8"
PIECE_WHITE_OUTLINE = "#2C3E55"
PIECE_BLACK_FILL = "#2C3E55"
PIECE_BLACK_OUTLINE = "#0E1622"

def _load_bundled_fonts() -> bool:
    """Manrope (body) + Fraunces (headings), the mockup's typefaces, are
    bundled in fonts/ (SIL Open Font License) and loaded privately for this
    process. Falls back to Segoe UI / Georgia if loading isn't possible."""
    font_dir = resource_dir() / "fonts"
    files = sorted(font_dir.glob("*.ttf")) if font_dir.is_dir() else []
    try:
        return bool(files) and all(ctk.FontManager.load_font(str(f)) for f in files)
    except Exception:
        return False


_FONTS_OK = _load_bundled_fonts()
FONT_FAMILY = "Manrope" if _FONTS_OK else "Segoe UI"
DISPLAY_FAMILY = "Fraunces" if _FONTS_OK else "Georgia"
FONT_BASE = (FONT_FAMILY, 13)
FONT_BOLD = (FONT_FAMILY, 13, "bold")
FONT_SMALL = (FONT_FAMILY, 11)
FONT_STATUS = (FONT_FAMILY, 15, "bold")
FONT_SECTION = (FONT_FAMILY, 12, "bold")
FONT_HEADING = (DISPLAY_FAMILY, 17, "bold")
FONT_TITLE = (DISPLAY_FAMILY, 24, "bold")
FONT_STAT = (FONT_FAMILY, 20, "bold")
# CustomTkinter sizes fonts in pixels, plain Tk in points — native widgets
# (Listbox, Treeview) use negative sizes (= pixels) so text matches
FONT_NATIVE = (FONT_FAMILY, -14)
FONT_NATIVE_BOLD = (FONT_FAMILY, -12, "bold")

RADIUS = 12

_LABEL_KINDS = {
    "title": (FONT_TITLE, TEXT),
    "heading": (FONT_HEADING, TEXT),
    "section": (FONT_SECTION, TEXT_SOFT),
    "status": (FONT_STATUS, TEXT),
    "body": (FONT_BASE, TEXT),
    "muted": (FONT_SMALL, TEXT_MUTED),
    "stat": (FONT_STAT, TEXT),
}


def apply_theme(root: tk.Misc) -> None:
    ctk.set_appearance_mode("dark")
    root.configure(fg_color=BG)

    # ttk widgets still in use (Treeview in Stats, scrollbars) — dark to match
    style = ttk.Style(root)
    style.theme_use("clam")
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])   # no border
    style.configure("Treeview", background=PANEL_ALT, fieldbackground=PANEL_ALT,
                    foreground=TEXT, font=FONT_NATIVE, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=PANEL_BG, foreground=TEXT_MUTED,
                    font=FONT_NATIVE_BOLD, borderwidth=0, relief="flat", padding=(4, 4))
    style.map("Treeview", background=[("selected", CONTROL_BG)], foreground=[("selected", TEXT)])
    style.map("Treeview.Heading", background=[("active", PANEL_ALT)])
    style.configure("Vertical.TScrollbar", background=CONTROL_BG, troughcolor=PANEL_BG,
                    bordercolor=PANEL_BG, arrowcolor=TEXT_MUTED, relief="flat")


# ---------- widget factories (keep every screen consistent) ----------

def label(master, text: str = "", kind: str = "body", **kwargs) -> ctk.CTkLabel:
    font, color = _LABEL_KINDS[kind]
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    kwargs.setdefault("text_color", color)
    return ctk.CTkLabel(master, text=text, font=font, **kwargs)


def card(master, **kwargs) -> ctk.CTkFrame:
    """Rounded, bordered panel used to group controls."""
    kwargs.setdefault("fg_color", PANEL_BG)
    kwargs.setdefault("corner_radius", 14)
    kwargs.setdefault("border_width", 1)
    kwargs.setdefault("border_color", BORDER)
    return ctk.CTkFrame(master, **kwargs)


def button(master, text: str, command=None, kind: str = "secondary", **kwargs) -> ctk.CTkButton:
    """kind: 'primary' (amber), 'secondary' (outlined), 'ghost' (text only)."""
    styles = {
        "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                        text_color_disabled="#6B5A3A", font=FONT_BOLD, border_width=0),
        "secondary": dict(fg_color=PANEL_ALT, hover_color=CONTROL_BG, text_color=TEXT,
                          text_color_disabled=TEXT_MUTED, font=FONT_BOLD,
                          border_width=1, border_color=BORDER_STRONG),
        "ghost": dict(fg_color="transparent", hover_color=CONTROL_BG, text_color=TEXT_SOFT,
                      font=FONT_BASE, border_width=0),
    }
    options = {**styles[kind], "height": 40, "corner_radius": 10, **kwargs}
    return ctk.CTkButton(master, text=text, command=command, **options)


def entry(master, textvariable=None, width: int = 90, **kwargs) -> ctk.CTkEntry:
    return ctk.CTkEntry(master, textvariable=textvariable, width=width, height=34,
                        fg_color=INPUT_BG, border_color=BORDER_STRONG, text_color=TEXT,
                        corner_radius=8, font=FONT_BASE, **kwargs)


def option_menu(master, values, variable=None, command=None, width: int = 160, **kwargs) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        master, values=values, variable=variable, command=command, width=width, height=34,
        fg_color=PANEL_ALT, button_color=CONTROL_BG, button_hover_color=BORDER_STRONG,
        text_color=TEXT, dropdown_fg_color=PANEL_ALT, dropdown_hover_color=CONTROL_BG,
        dropdown_text_color=TEXT, corner_radius=8, font=FONT_BASE, dropdown_font=FONT_BASE,
        dynamic_resizing=False, **kwargs,
    )


def progress_bar(master) -> ctk.CTkProgressBar:
    return ctk.CTkProgressBar(master, height=6, corner_radius=3, fg_color=CONTROL_BG,
                              progress_color=SUCCESS)


def listbox(master, **kwargs) -> tk.Listbox:
    """CustomTkinter has no list widget — a flat dark tk.Listbox instead."""
    return tk.Listbox(
        master, activestyle="none", font=FONT_NATIVE, bg=PANEL_ALT, fg=TEXT,
        selectbackground=CONTROL_BG, selectforeground=TEXT, relief="flat", borderwidth=0,
        highlightthickness=0, exportselection=False, **kwargs,
    )
