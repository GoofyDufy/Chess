"""The analysis board shared by Puzzle Review, Sacrifice!! and Game Review,
and the panel of Stockfish's ideas that Opening Drill's Tree shows too.

IdeasPanel: Stockfish's top 3 lines, a row each, its dot in its arrow's
colour; clicking a row plays that line (on_pick(i)).

Analysis: a board of its own on the screen's ChessBoardWidget, from any
position. Moves can be played for either side; ◀ takes back (not past
`floor`), ▶ plays the next move of `ahead` (the game / the solution / a
clicked Stockfish line), else Stockfish's best move. peek(board): just
Stockfish's lines for a position the screen shows (Game Review's top moves). Stockfish runs on a
background thread (engine_ideas.py); its answers land here on the UI thread.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import chess
import customtkinter as ctk

import engine_ideas
import theme
from board_widget import BOARD_PIXELS, GAME_MOVE_COLOR, REPLY_COLOR

IDEA_COLORS = [REPLY_COLOR, GAME_MOVE_COLOR, "#8A8F98"]   # Stockfish's 1st / 2nd / 3rd move
_RANKS = ("Best", "2nd", "3rd")


def idea_arrows(ideas) -> list:
    """The ideas as marked arrows, best drawn last (on top)."""
    return [(idea.move.from_square, idea.move.to_square, IDEA_COLORS[i]) for i, idea in enumerate(ideas)][::-1]


def idea_rows(fen: str, ideas) -> List[str]:
    board = chess.Board(fen)
    return [f"{idea.eval_text()}   {engine_ideas.line_text(board, idea.pv)}" for idea in ideas]


def idea_chips(fen: str, ideas) -> List[str]:
    """Short form, one move each: '+0.32 d5'."""
    board = chess.Board(fen)
    return [f"{idea.eval_text()} {board.san(idea.move)}" for idea in ideas]


def game_over_text(board: chess.Board) -> str:
    winner = board.outcome().winner
    return "Game over: " + ("draw" if winner is None else ("White" if winner else "Black") + " wins") + "."


class IdeasPanel(ctk.CTkFrame):
    """title: a heading line (False: none). header_beside: the status text
    next to the title instead of under it (saves a line on short screens)."""

    def __init__(self, master, on_pick: Callable[[int], None], title: bool = True,
                 header_beside: bool = False, row_height: Optional[int] = 20):
        super().__init__(master, fg_color="transparent")
        sized = {"height": row_height} if row_height else {}
        self.title_label = None
        if title:
            title_row = ctk.CTkFrame(self, fg_color="transparent")
            title_row.pack(anchor="w", fill="x")
            self.title_label = theme.label(title_row, "", "status", height=20)
            self.title_label.pack(side="left")
        if title and header_beside:
            self.header_label = theme.label(title_row, "", "muted", height=20)
            self.header_label.pack(side="left", padx=(10, 0))
        else:
            self.header_label = theme.label(self, "", "muted", wraplength=BOARD_PIXELS,
                                            **({"height": 18} if title else {}))
            self.header_label.pack(anchor="w")
        self._rows = []
        for i, rank in enumerate(_RANKS):
            row = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
            dot = ctk.CTkLabel(row, text=f"● {rank}", width=46, anchor="w", font=theme.FONT_BOLD,
                               text_color=IDEA_COLORS[i] if i < 2 else "#A9AFBA", cursor="hand2", **sized)
            dot.pack(side="left")
            text = theme.label(row, "", "body", wraplength=BOARD_PIXELS - 50, cursor="hand2", **sized)
            text.pack(side="left", fill="x")
            for widget in (row, dot, text):
                widget.bind("<Button-1>", lambda e, i=i: on_pick(i))
            self._rows.append((row, text))

    def set_title(self, text: str) -> None:
        if self.title_label is not None:
            self.title_label.configure(text=text)

    def show(self, header: str, rows: List[str] = ()) -> None:
        self.header_label.configure(text=header)
        for i, (row, text) in enumerate(self._rows):
            if i < len(rows):
                text.configure(text=rows[i])
                row.pack(anchor="w", fill="x")
            else:
                row.pack_forget()


class TopMovesRow(ctk.CTkFrame):
    """One line: 'Stockfish:  ● +0.32 d5   ● +0.10 d6   ● -0.05 c5', each in
    its arrow's colour, clickable (on_pick(i)). For screens with no room for
    the full IdeasPanel."""

    def __init__(self, master, on_pick: Callable[[int], None]):
        super().__init__(master, fg_color="transparent")
        self.lead = theme.label(self, "", "muted", height=20)
        self.lead.pack(side="left")
        self._chips = []
        for i in range(len(_RANKS)):
            chip = ctk.CTkLabel(self, text="", height=20, anchor="w", font=theme.FONT_BOLD, cursor="hand2",
                                text_color=IDEA_COLORS[i] if i < 2 else "#A9AFBA")
            chip.bind("<Button-1>", lambda e, i=i: on_pick(i))
            self._chips.append(chip)

    def set_title(self, text: str) -> None:
        pass

    def show(self, header: str, rows: List[str] = ()) -> None:
        self.lead.configure(text=header)
        for i, chip in enumerate(self._chips):
            if i < len(rows):
                chip.configure(text=f"● {rows[i]}")
                chip.pack(side="left", padx=(10, 0))
            else:
                chip.pack_forget()


class Analysis:
    """owner: the screen (for after() and <Destroy>). flipped(): board
    orientation. title(board): the panel's heading for a position.
    on_shown(board): after each new position (e.g. to select it in a move list)."""

    def __init__(self, owner, board_widget, panel: IdeasPanel, eval_bar=None, *,
                 flipped: Optional[Callable[[], bool]] = None,
                 title: Optional[Callable[[chess.Board], str]] = None,
                 hint: str = "click a line, then ▶",
                 on_shown: Optional[Callable[[chess.Board], None]] = None,
                 peek_panel: Optional[TopMovesRow] = None):
        self.board_widget = board_widget
        self.panel = panel
        self.peek_panel = peek_panel or panel   # where peek() shows its lines
        self.eval_bar = eval_bar
        self._flipped = flipped or (lambda: board_widget.flipped)
        self._title = title or (lambda board: ("White" if board.turn == chess.WHITE else "Black") + " to move")
        self.hint = hint
        self.on_shown = on_shown
        self.active = False
        self.peeking = False                  # ideas for a position shown elsewhere (see peek)
        self.board = chess.Board()
        self.floor = 0                        # moves on the board ◀ can't take back
        self.ahead: List[chess.Move] = []     # ▶ plays these first
        self.ideas: list = []
        self._fen: Optional[str] = None
        self.engine = engine_ideas.IdeasEngine(owner, self._on_ideas, self._on_error)
        owner.bind("<Destroy>", lambda e: self.engine.stop(), add="+")

    # ---------- the board ----------

    def start(self, board: chess.Board, ahead=(), floor: int = 0) -> None:
        """Analyse from this position (the board is copied); shows it."""
        self.active = True
        self.board = board.copy()
        self.ahead = list(ahead)
        self.floor = floor
        self.show()

    def stop(self) -> None:
        self.active = False
        self._fen = None
        self.ideas = []

    def play(self, move: chess.Move) -> bool:
        """A move on the board (either side). The `ahead` line is kept if it was its next move."""
        if not self.active or move not in self.board.legal_moves:
            return False
        self.ahead = self.ahead[1:] if self.ahead and self.ahead[0] == move else []
        self.board.push(move)
        self.show()
        return True

    def step(self, delta: int, show: bool = True) -> bool:
        """◀ (delta < 0) / ▶ (delta > 0) one move; False when there's nothing to do."""
        if not self.active:
            return False
        board = self.board
        if delta < 0:
            if len(board.move_stack) <= self.floor:
                return False
            self.ahead.insert(0, board.pop())          # ▶ plays it again
        else:
            move = self.ahead[0] if self.ahead else (self.ideas[0].move if self.ideas else None)
            if move is None or move not in board.legal_moves:
                return False
            self.ahead = self.ahead[1:]
            board.push(move)
            self.ideas = []                            # a new position: fresh ideas before ▶ follows them
        if show:
            self.show()
        return True

    def walk(self, steps: int) -> None:
        """Several steps (⏮ / ⏭), the board shown once at the end."""
        for _ in range(min(abs(steps), 999)):
            if not self.step(1 if steps > 0 else -1, show=False):
                break
        self.show()

    def follow(self, i: int) -> None:
        """A Stockfish line clicked: play its first move; ▶ plays on along it."""
        if not self.active or i >= len(self.ideas):
            return
        pv = list(self.ideas[i].pv)
        if self.play(pv[0]):
            self.ahead = pv[1:]

    def show(self) -> None:
        board = self.board
        self.board_widget.show_board(board.copy(), flipped=self._flipped())
        self.panel.set_title(f"Analysis — {self._title(board)}")
        self._fen = board.fen()
        self.ideas = []
        self.board_widget.set_marked_arrows([])
        if self.on_shown:
            self.on_shown(board)
        if board.is_game_over():
            self.panel.show(game_over_text(board))
            return
        self.panel.show("Stockfish is thinking…")
        self.engine.analyse(self._fen)

    def peek(self, board: chess.Board) -> None:
        """Stockfish's top moves for a position the screen shows itself (not
        the analysis board), in peek_panel and as arrows; the board and eval
        bar are left alone."""
        self.peeking = True
        self._fen = board.fen()
        self.ideas = []
        self.board_widget.set_marked_arrows([])
        if board.is_game_over():
            self.peek_panel.show(game_over_text(board))
            return
        self.peek_panel.show("Stockfish is thinking…")
        self.engine.analyse(self._fen)

    def unpeek(self) -> None:
        self.peeking = False
        if not self.active:
            self._fen = None
            self.ideas = []

    # ---------- Stockfish ----------

    def _on_ideas(self, fen: str, depth: int, ideas) -> None:
        if not (self.active or self.peeking) or fen != self._fen or not ideas:
            return                                     # moved on / stopped
        self.ideas = ideas
        if self.active:
            self.panel.show(f"Stockfish depth {depth} · {self.hint}", idea_rows(fen, ideas))
        elif isinstance(self.peek_panel, TopMovesRow):
            self.peek_panel.show("Stockfish:", idea_chips(fen, ideas))
        else:
            self.peek_panel.show(f"Stockfish depth {depth}", idea_rows(fen, ideas))
        self.board_widget.set_marked_arrows(idea_arrows(ideas))
        if self.eval_bar is not None and self.active:  # peeking: the screen's own evaluation stays
            self.eval_bar.set(ideas[0].cp_white, flipped=self._flipped())

    def _on_error(self, message: str) -> None:
        (self.panel if self.active else self.peek_panel).show(message)
