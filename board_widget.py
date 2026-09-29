"""Shared clickable chessboard canvas widget."""

from __future__ import annotations

import tkinter as tk
from typing import Optional

import chess

from piece_shapes import draw_piece
from theme import (
    BOARD_DARK, BOARD_HIGHLIGHT, BOARD_LIGHT, LAST_MOVE_DARK, LAST_MOVE_LIGHT,
    PANEL_BG, )

SQUARE_SIZE = 64
BOARD_PIXELS = SQUARE_SIZE * 8
LIGHT = BOARD_LIGHT
DARK = BOARD_DARK
HIGHLIGHT = BOARD_HIGHLIGHT
ANNOTATION_COLOR = "#E0892B"   # orange, reads on both square colors



class ChessBoardWidget(tk.Canvas):
    """Clickable board: click a piece, then click a destination square.
    Calls on_move_attempt(move) for any legal move you complete. The
    caller decides whether the move is "correct" and whether to reflect
    it on the board (via load_fen / push_move) or reset the selection."""

    def __init__(self, master, on_move_attempt, **kwargs):
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("highlightbackground", "#262A32")
        kwargs.setdefault("bg", PANEL_BG)
        super().__init__(master, width=BOARD_PIXELS, height=BOARD_PIXELS, **kwargs)
        self.on_move_attempt = on_move_attempt
        self.board: Optional[chess.Board] = None
        self.selected_square: Optional[int] = None
        self.legal_targets: set[int] = set()
        # True = Black at the bottom (you're playing Black)
        self.flipped: bool = False
        # chess.com-style annotations: right-drag = arrow, right-click a
        # square = circle, any left click clears them
        self.arrows: set[tuple[int, int]] = set()
        self.circles: set[int] = set()
        self._right_press_square: Optional[int] = None
        self.bind("<Button-1>", self._handle_click)
        # Button-3 is right-click on Windows/Linux; Button-2 on macOS
        for button in ("3", "2"):
            self.bind(f"<ButtonPress-{button}>", self._on_right_press)
            self.bind(f"<ButtonRelease-{button}>", self._on_right_release)

    def load_fen(self, fen: str, flipped: Optional[bool] = None) -> None:
        self.board = chess.Board(fen)
        if flipped is not None:
            self.flipped = flipped
        self.arrows.clear()
        self.circles.clear()
        self.selected_square = None
        self.legal_targets = set()
        self.redraw()

    def show_board(self, board: chess.Board, flipped: Optional[bool] = None) -> None:
        """Display an existing board object directly (used by the drill
        tab, which maintains one running game rather than loading a FEN
        per puzzle)."""
        self.board = board
        if flipped is not None:
            self.flipped = flipped
        self.arrows.clear()
        self.circles.clear()
        self.selected_square = None
        self.legal_targets = set()
        self.redraw()

    def clear(self) -> None:
        self.board = None
        self.selected_square = None
        self.legal_targets = set()
        self.arrows.clear()
        self.circles.clear()
        self.delete("all")

    def _square_at(self, col: int, row: int) -> int:
        """Board square shown at a screen column/row (0,0 = top-left)."""
        if self.flipped:
            return chess.square(7 - col, row)
        return chess.square(col, 7 - row)

    def _square_center(self, square: int) -> tuple[float, float]:
        """Inverse of _square_at: pixel center of a square on screen."""
        file, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (7 - file, rank) if self.flipped else (file, 7 - rank)
        return col * SQUARE_SIZE + SQUARE_SIZE / 2, row * SQUARE_SIZE + SQUARE_SIZE / 2

    def _event_square(self, event) -> Optional[int]:
        col, row = event.x // SQUARE_SIZE, event.y // SQUARE_SIZE
        if not (0 <= col <= 7 and 0 <= row <= 7):
            return None
        return self._square_at(col, row)

    # ---------- annotations (arrows / circles) ----------

    def _on_right_press(self, event) -> None:
        self._right_press_square = self._event_square(event) if self.board else None

    def _on_right_release(self, event) -> None:
        start, end = self._right_press_square, self._event_square(event)
        self._right_press_square = None
        if start is None or end is None:
            return
        if start == end:
            self.circles ^= {start}          # right-click again removes it
        else:
            self.arrows ^= {(start, end)}    # same arrow again removes it
        self.redraw()

    def _draw_annotations(self) -> None:
        r = SQUARE_SIZE / 2 - 3
        for square in self.circles:
            cx, cy = self._square_center(square)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ANNOTATION_COLOR, width=4)
        for start, end in self.arrows:
            x0, y0 = self._square_center(start)
            x1, y1 = self._square_center(end)
            self.create_line(x0, y0, x1, y1, fill=ANNOTATION_COLOR, width=11,
                             arrow="last", arrowshape=(22, 26, 10), capstyle="round")

    def redraw(self) -> None:
        self.delete("all")
        if self.board is None:
            return
        last_move = self.board.move_stack[-1] if self.board.move_stack else None
        last_squares = {last_move.from_square, last_move.to_square} if last_move else set()

        for row in range(8):
            for col in range(8):
                square = self._square_at(col, row)
                x0, y0 = col * SQUARE_SIZE, row * SQUARE_SIZE
                x1, y1 = x0 + SQUARE_SIZE, y0 + SQUARE_SIZE
                is_light = (col + row) % 2 == 0
                if square == self.selected_square:
                    color = HIGHLIGHT
                elif square in last_squares:
                    color = LAST_MOVE_LIGHT if is_light else LAST_MOVE_DARK
                else:
                    color = LIGHT if is_light else DARK
                self.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

                # subtle rank/file coordinates along the edges, like most
                # chess UIs — helps orient at a glance
                label_color = DARK if is_light else LIGHT
                if col == 0:
                    self.create_text(
                        x0 + 6, y0 + 8, text=str(chess.square_rank(square) + 1), anchor="nw",
                        font=("Arial", 8, "bold"), fill=label_color,
                    )
                if row == 7:
                    self.create_text(
                        x1 - 6, y1 - 6, text=chess.FILE_NAMES[chess.square_file(square)], anchor="se",
                        font=("Arial", 8, "bold"), fill=label_color,
                    )

                piece = self.board.piece_at(square)
                if piece:
                    draw_piece(self, piece, x0, y0, SQUARE_SIZE)

                # legal-move indicator: a dot for empty squares, a ring
                # around the piece for captures — this is the main fix for
                # "clicking doesn't seem to do anything": now you can see
                # exactly where a selected piece is allowed to go.
                if square in self.legal_targets:
                    cx, cy = x0 + SQUARE_SIZE / 2, y0 + SQUARE_SIZE / 2
                    if piece:
                        r = SQUARE_SIZE / 2 - 4
                        self.create_oval(
                            cx - r, cy - r, cx + r, cy + r,
                            outline="#6B7280", width=4,
                        )
                    else:
                        r = 9
                        self.create_oval(
                            cx - r, cy - r, cx + r, cy + r,
                            fill="#8A8F98", outline="",
                        )

        self._draw_annotations()

    def _flash_illegal(self) -> None:
        """Brief red flash on the whole board so an invalid click is
        obviously acknowledged instead of silently doing nothing."""
        self.configure(highlightbackground="#C0392B", highlightthickness=2)
        self.after(150, lambda: self.configure(highlightthickness=0))

    def _handle_click(self, event) -> None:
        if self.board is None:
            return
        if self.arrows or self.circles:
            # like chess.com: a left click wipes your drawings, then the
            # click still works as a normal select/move click
            self.arrows.clear()
            self.circles.clear()
            self.redraw()
        square = self._event_square(event)
        if square is None:
            return

        if self.selected_square is None:
            piece = self.board.piece_at(square)
            if piece and piece.color == self.board.turn:
                self.selected_square = square
                self.legal_targets = {
                    m.to_square for m in self.board.legal_moves
                    if m.from_square == square
                }
                self.redraw()
            return

        # clicking your own piece again — reselect instead of failing
        piece_at_target = self.board.piece_at(square)
        if piece_at_target and piece_at_target.color == self.board.turn and square != self.selected_square:
            self.selected_square = square
            self.legal_targets = {
                m.to_square for m in self.board.legal_moves
                if m.from_square == square
            }
            self.redraw()
            return

        # clicking the already-selected square — deselect
        if square == self.selected_square:
            self.selected_square = None
            self.legal_targets = set()
            self.redraw()
            return

        move = chess.Move(self.selected_square, square)
        if move not in self.board.legal_moves:
            promo_move = chess.Move(self.selected_square, square, promotion=chess.QUEEN)
            if promo_move in self.board.legal_moves:
                move = promo_move

        if move in self.board.legal_moves:
            self.selected_square = None
            self.legal_targets = set()
            self.redraw()
            self.on_move_attempt(move)
        else:
            # not a legal destination — flash instead of silently
            # deselecting, and keep the piece selected so the user can
            # just try a different square right away
            self._flash_illegal()
