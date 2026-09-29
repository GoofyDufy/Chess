"""Vector chess pieces ("Cool slate" set from the Claude Design canvas),
drawn with plain Tk canvas shapes — polygons, circles, ovals, rectangles
and one line — so they're sharp at any size and need no image files.

Coordinates are in a 100 x 100 box (the design's SVG viewBox), drawn back
to front exactly as in the design, then scaled into a board square.
"""

from __future__ import annotations

import tkinter as tk

import chess

import theme

# (kind, *args) in the 100x100 design space; drawn in list order
BASE = ("rect", 24, 74, 52, 12)
SHAPES = {
    chess.PAWN: [
        ("circle", 50, 30, 12),
        ("poly", [39, 44, 61, 44, 67, 74, 33, 74]),
        ("rect", 26, 74, 48, 12),
    ],
    chess.ROOK: [
        ("poly", [34, 34, 66, 34, 68, 74, 32, 74]),
        ("poly", [28, 14, 37, 14, 37, 21, 45, 21, 45, 14, 55, 14, 55, 21, 63, 21, 63, 14, 72, 14, 72, 34, 28, 34]),
        BASE,
    ],
    chess.BISHOP: [
        ("poly", [40, 54, 60, 54, 66, 74, 34, 74]),
        ("ellipse", 50, 38, 15, 18),
        ("circle", 50, 14, 6),
        ("slit", 44, 30, 56, 42),          # the mitre's cut, in the outline color
        BASE,
    ],
    chess.KNIGHT: [
        ("poly", [30, 74, 34, 56, 45, 46, 38, 42, 30, 46, 25, 40, 28, 30, 42, 18, 48, 10, 54, 18,
                  66, 24, 73, 40, 71, 74]),
        ("eye", 52, 28, 3),                # filled with the outline color
        BASE,
    ],
    chess.QUEEN: [
        ("poly", [24, 36, 35, 56, 40, 24, 50, 50, 60, 24, 65, 56, 76, 36, 68, 70, 32, 70]),
        ("circle", 24, 33, 5), ("circle", 40, 21, 5), ("circle", 60, 21, 5), ("circle", 76, 33, 5),
        ("rect", 28, 70, 44, 6),
        ("rect", 24, 76, 52, 10),
    ],
    chess.KING: [
        ("poly", [30, 34, 70, 34, 64, 70, 36, 70]),
        ("rect", 46, 6, 8, 24),
        ("rect", 38, 12, 24, 8),
        ("rect", 28, 70, 44, 6),
        ("rect", 24, 76, 52, 10),
    ],
}

STROKE_UNITS = 3.0       # outline width in design units (Cool slate: 3)
PIECE_SCALE = 1.15       # pieces span y 6..86 of the 100-box -> ~92% of the square
CONTENT_CENTER = (50, 46)   # centre of the pieces' drawn area in design units


def draw_piece(canvas: tk.Canvas, piece: chess.Piece, x0: float, y0: float, size: float) -> None:
    """Draw one piece into the square whose top-left corner is (x0, y0)."""
    white = piece.color == chess.WHITE
    fill = theme.PIECE_WHITE_FILL if white else theme.PIECE_BLACK_FILL
    outline = theme.PIECE_WHITE_OUTLINE if white else theme.PIECE_BLACK_OUTLINE
    s = size * PIECE_SCALE / 100
    ox = x0 + size / 2 - CONTENT_CENTER[0] * s
    oy = y0 + size / 2 - CONTENT_CENTER[1] * s
    width = max(1.0, STROKE_UNITS * s)

    def pt(x: float, y: float):
        return ox + x * s, oy + y * s

    for kind, *a in SHAPES[piece.piece_type]:
        if kind == "poly":
            coords = [c for i in range(0, len(a[0]), 2) for c in pt(a[0][i], a[0][i + 1])]
            canvas.create_polygon(*coords, fill=fill, outline=outline, width=width, joinstyle="round")
        elif kind in ("circle", "eye"):
            cx, cy, r = a
            canvas.create_oval(*pt(cx - r, cy - r), *pt(cx + r, cy + r),
                               fill=outline if kind == "eye" else fill,
                               outline=outline, width=width if kind == "circle" else 0)
        elif kind == "ellipse":
            cx, cy, rx, ry = a
            canvas.create_oval(*pt(cx - rx, cy - ry), *pt(cx + rx, cy + ry),
                               fill=fill, outline=outline, width=width)
        elif kind == "rect":
            x, y, w, h = a
            canvas.create_rectangle(*pt(x, y), *pt(x + w, y + h), fill=fill, outline=outline, width=width)
        elif kind == "slit":
            canvas.create_line(*pt(a[0], a[1]), *pt(a[2], a[3]), fill=outline,
                               width=max(1.0, 3.5 * s), capstyle="round")
