"""Shared clickable chessboard canvas widget."""

from __future__ import annotations

import tkinter as tk
from typing import Optional

import chess

from theme import (
    BOARD_DARK, BOARD_HIGHLIGHT, BOARD_LIGHT, LAST_MOVE_DARK, LAST_MOVE_LIGHT,
    PANEL_BG, PIECE_BLACK_FILL, PIECE_BLACK_OUTLINE, PIECE_WHITE_FILL,
    PIECE_WHITE_OUTLINE,
)

SQUARE_SIZE = 64
BOARD_PIXELS = SQUARE_SIZE * 8
LIGHT = BOARD_LIGHT
DARK = BOARD_DARK
HIGHLIGHT = BOARD_HIGHLIGHT

# Same unicode glyph is used for both colors — we recolor it ourselves
# below rather than relying on the font's built-in hollow/solid styling,
# so pieces read as "cream" vs "dark brown" like the rest of the theme.
UNICODE_PIECES = {
    "P": "\u265F", "N": "\u265E", "B": "\u265D", "R": "\u265C", "Q": "\u265B", "K": "\u265A",
    "p": "\u265F", "n": "\u265E", "b": "\u265D", "r": "\u265C", "q": "\u265B", "k": "\u265A",
}


class ChessBoardWidget(tk.Canvas):
    """Clickable board: click a piece, then click a destination square.
    Calls on_move_attempt(move) for any legal move you complete. The
    caller decides whether the move is "correct" and whether to reflect
    it on the board (via load_fen / push_move) or reset the selection."""

    def __init__(self, master, on_move_attempt, **kwargs):
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", "#D9DCE3")
        kwargs.setdefault("bg", PANEL_BG)
        super().__init__(master, width=BOARD_PIXELS, height=BOARD_PIXELS, **kwargs)
        self.on_move_attempt = on_move_attempt
        self.board: Optional[chess.Board] = None
        self.selected_square: Optional[int] = None
        self.legal_targets: set[int] = set()
        # True = Black at the bottom (you're playing Black)
        self.flipped: bool = False
        self.bind("<Button-1>", self._handle_click)

    def load_fen(self, fen: str, flipped: Optional[bool] = None) -> None:
        self.board = chess.Board(fen)
        if flipped is not None:
            self.flipped = flipped
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
        self.selected_square = None
        self.legal_targets = set()
        self.redraw()

    def clear(self) -> None:
        self.board = None
        self.selected_square = None
        self.legal_targets = set()
        self.delete("all")

    def _square_at(self, col: int, row: int) -> int:
        """Board square shown at a screen column/row (0,0 = top-left)."""
        if self.flipped:
            return chess.square(7 - col, row)
        return chess.square(col, 7 - row)

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
                    symbol = UNICODE_PIECES[piece.symbol()]
                    cx, cy = x0 + SQUARE_SIZE / 2, y0 + SQUARE_SIZE / 2
                    if piece.color == chess.WHITE:
                        fill, outline = PIECE_WHITE_FILL, PIECE_WHITE_OUTLINE
                    else:
                        fill, outline = PIECE_BLACK_FILL, PIECE_BLACK_OUTLINE
                    font = ("Arial", 38)
                    # poor-man's text outline: draw the glyph offset in
                    # every direction in the outline color first, then
                    # draw it again centered in the fill color on top —
                    # gives pieces a readable edge against either square
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
                        self.create_text(cx + dx, cy + dy, text=symbol, font=font, fill=outline)
                    self.create_text(cx, cy, text=symbol, font=font, fill=fill)

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

    def _flash_illegal(self) -> None:
        """Brief red flash on the whole board so an invalid click is
        obviously acknowledged instead of silently doing nothing."""
        self.configure(highlightbackground="#C0392B", highlightthickness=2)
        self.after(150, lambda: self.configure(highlightbackground="#D9DCE3", highlightthickness=1))

    def _handle_click(self, event) -> None:
        if self.board is None:
            return
        col = event.x // SQUARE_SIZE
        row = event.y // SQUARE_SIZE
        if not (0 <= col <= 7 and 0 <= row <= 7):
            return
        square = self._square_at(col, row)

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
