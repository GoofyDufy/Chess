"""Tiny canvas charts in the app theme (no plotting library needed):
a line chart with an optional goal line, and a two-part bar chart."""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional, Tuple

import theme

PAD_L, PAD_R, PAD_T, PAD_B = 44, 12, 12, 22


def _frame(canvas: tk.Canvas) -> Tuple[int, int]:
    canvas.delete("all")
    w = max(canvas.winfo_width(), int(canvas.cget("width")))
    h = max(canvas.winfo_height(), int(canvas.cget("height")))
    return w, h


def _empty(canvas: tk.Canvas, w: int, h: int, text: str) -> None:
    canvas.create_text(w / 2, h / 2, text=text, fill=theme.TEXT_MUTED, font=theme.FONT_NATIVE)


def line_chart(canvas: tk.Canvas, points: List[Tuple[str, float]], goal: Optional[float] = None,
               color: str = theme.SUCCESS, value_fmt: str = "{:.0f}", empty_text: str = "No data yet") -> None:
    """points: (x label, value) in order. The goal (if any) is drawn as a
    dashed amber line and always kept in view."""
    w, h = _frame(canvas)
    if len(points) < 2:
        _empty(canvas, w, h, empty_text)
        return
    values = [v for _, v in points] + ([goal] if goal is not None else [])
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    plot_w, plot_h = w - PAD_L - PAD_R, h - PAD_T - PAD_B

    def xy(i: int, v: float) -> Tuple[float, float]:
        return PAD_L + plot_w * i / (len(points) - 1), PAD_T + plot_h * (1 - (v - lo) / (hi - lo))

    for frac in (0, 0.5, 1):        # light gridlines with value labels
        v = lo + (hi - lo) * frac
        _, y = xy(0, v)
        canvas.create_line(PAD_L, y, w - PAD_R, y, fill=theme.BORDER)
        canvas.create_text(PAD_L - 6, y, text=value_fmt.format(v), anchor="e",
                           fill=theme.TEXT_MUTED, font=theme.FONT_NATIVE_BOLD)
    if goal is not None:
        _, gy = xy(0, goal)
        canvas.create_line(PAD_L, gy, w - PAD_R, gy, fill=theme.ACCENT, dash=(5, 4), width=2)
        canvas.create_text(w - PAD_R, gy - 3, text=f"Goal {value_fmt.format(goal)}", anchor="se",
                           fill=theme.ACCENT, font=theme.FONT_NATIVE_BOLD)
    coords = [c for i, (_, v) in enumerate(points) for c in xy(i, v)]
    canvas.create_line(*coords, fill=color, width=2, smooth=True)
    lx, ly = xy(len(points) - 1, points[-1][1])
    canvas.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=color, outline="")
    for i, anchor in ((0, "sw"), (len(points) // 2, "s"), (len(points) - 1, "se")):   # a few x labels
        x, _ = xy(i, lo)
        canvas.create_text(x, h - 4, text=points[i][0], anchor=anchor, fill=theme.TEXT_MUTED,
                           font=theme.FONT_NATIVE_BOLD)


def bar_chart(canvas: tk.Canvas, bars: List[Tuple[str, int, int]], labels: Tuple[str, str],
              empty_text: str = "No data yet") -> None:
    """bars: (x label, total, good part). Draws total in muted, good part
    in green on top, with a small legend."""
    w, h = _frame(canvas)
    if not bars:
        _empty(canvas, w, h, empty_text)
        return
    top = max(t for _, t, _ in bars) or 1
    plot_w, plot_h = w - PAD_L - PAD_R, h - PAD_T - PAD_B
    slot = plot_w / len(bars)
    bar_w = max(4, min(28, slot * 0.6))
    canvas.create_text(PAD_L - 6, PAD_T, text=str(top), anchor="ne", fill=theme.TEXT_MUTED,
                       font=theme.FONT_NATIVE_BOLD)
    for i, (label, total, good) in enumerate(bars):
        cx = PAD_L + slot * (i + 0.5)
        y_total = PAD_T + plot_h * (1 - total / top)
        y_good = PAD_T + plot_h * (1 - good / top)
        canvas.create_rectangle(cx - bar_w / 2, y_total, cx + bar_w / 2, PAD_T + plot_h,
                                fill=theme.CONTROL_BG, outline="")
        canvas.create_rectangle(cx - bar_w / 2, y_good, cx + bar_w / 2, PAD_T + plot_h,
                                fill=theme.SUCCESS, outline="")
        if len(bars) <= 8 or i in (0, len(bars) - 1):
            canvas.create_text(cx, h - 4, text=label, anchor="s", fill=theme.TEXT_MUTED,
                               font=theme.FONT_NATIVE_BOLD)
    canvas.create_rectangle(w - 150, 4, w - 140, 14, fill=theme.SUCCESS, outline="")
    canvas.create_text(w - 136, 9, text=labels[1], anchor="w", fill=theme.TEXT_MUTED, font=theme.FONT_NATIVE_BOLD)
    canvas.create_rectangle(w - 80, 4, w - 70, 14, fill=theme.CONTROL_BG, outline="")
    canvas.create_text(w - 66, 9, text=labels[0], anchor="w", fill=theme.TEXT_MUTED, font=theme.FONT_NATIVE_BOLD)
