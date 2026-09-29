"""Vertical evaluation bar shown beside the board (like chess.com): White's
share of the bar grows with White's advantage, and the bar flips with the
board so your side is always at the bottom."""

from __future__ import annotations

import math
import tkinter as tk
from typing import Optional

import theme
from analyzer import MATE_CP

BAR_WIDTH = 18
WHITE_COLOR = "#F2EFEA"
BLACK_COLOR = "#3A3632"


def white_share(cp_white: int) -> float:
    """Centipawns (White's view) -> fraction of the bar that is white.
    A logistic curve like the major sites: +1 pawn ~ 60%, +4 ~ 83%."""
    if cp_white >= MATE_CP:
        return 1.0
    if cp_white <= -MATE_CP:
        return 0.0
    return 1 / (1 + math.exp(-0.004 * cp_white))


class EvalBar(tk.Canvas):
    def __init__(self, master, height: int, **kwargs):
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bg", BLACK_COLOR)
        super().__init__(master, width=BAR_WIDTH, height=height, **kwargs)
        self.bar_height = height
        self.cp_white: Optional[int] = None
        self.flipped = False
        self.redraw()

    def set(self, cp_white: Optional[int], flipped: Optional[bool] = None) -> None:
        """cp_white: evaluation from White's point of view (None = unknown)."""
        self.cp_white = cp_white
        if flipped is not None:
            self.flipped = flipped
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        h, w = self.bar_height, BAR_WIDTH
        if self.cp_white is None:
            self.create_rectangle(0, 0, w, h, fill=theme.CONTROL_BG, outline="")
            return
        white_px = h * white_share(self.cp_white)
        # White fills from White's side: the bottom normally, the top when flipped
        if self.flipped:
            self.create_rectangle(0, 0, w, h, fill=BLACK_COLOR, outline="")
            self.create_rectangle(0, 0, w, white_px, fill=WHITE_COLOR, outline="")
        else:
            self.create_rectangle(0, 0, w, h, fill=BLACK_COLOR, outline="")
            self.create_rectangle(0, h - white_px, w, h, fill=WHITE_COLOR, outline="")
        self.create_line(0, h / 2, w, h / 2, fill=theme.TEXT_MUTED)   # the 0.0 mark
        # number at the leading side's end, in contrasting color
        cp = self.cp_white
        if abs(cp) >= MATE_CP:
            text = "M"
        else:
            text = f"{abs(cp) / 100:.1f}"
        white_ahead = cp >= 0
        at_bottom = white_ahead != self.flipped
        y = h - 4 if at_bottom else 4
        self.create_text(w / 2, y, text=text, anchor="s" if at_bottom else "n",
                         fill=BLACK_COLOR if white_ahead else WHITE_COLOR, font=(theme.FONT_FAMILY, -9, "bold"))
