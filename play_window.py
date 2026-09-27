"""'Play it out' window: play any position against Stockfish at an
adjustable strength. Used from puzzles (and the endgame filter) and at the
end of a drilled opening line.

Threading follows the app-wide rule (see puzzle_tab.py): the engine lives
on ONE background thread that takes jobs from a queue; results come back
through a second queue drained by an after() poller on the main thread.
No Tk call ever happens off the main thread.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

import chess
import chess.engine
import customtkinter as ctk

import engine_locator
import theme
from board_widget import BOARD_PIXELS, ChessBoardWidget

# label -> UCI_Elo (None = full strength). Stockfish's minimum Elo is ~1320.
STRENGTHS = {
    "Club (1400)": 1400,
    "Intermediate (1700)": 1700,
    "Strong (2000)": 2000,
    "Expert (2300)": 2300,
    "Full strength": None,
}
THINK_SECONDS = 0.6


class PlayWindow(ctk.CTkToplevel):
    def __init__(self, master, fen: str, title: str = "Play it out",
                 my_color: Optional[chess.Color] = None, conn=None):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=theme.BG)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.after(50, self.lift)

        self.start_fen = fen
        self.board = chess.Board(fen)
        self.my_color = self.board.turn if my_color is None else my_color
        self.conn = conn
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._thinking = False
        self._closed = False

        self._build(title)
        self.protocol("WM_DELETE_WINDOW", self._close)

        path = engine_locator.find_stockfish()
        if path is None:
            self._set_status("Stockfish not found — see Puzzle Review for setup help.", theme.ERROR)
        else:
            threading.Thread(target=self._engine_loop, args=(path,), daemon=True).start()
            self.after(100, self._poll)
        self._refresh()
        if self.board.turn != self.my_color:
            self._request_engine_move()

    # ---------- layout ----------

    def _build(self, title: str) -> None:
        board_card = theme.card(self)
        board_card.pack(side="left", padx=(20, 10), pady=20)
        self.board_widget = ChessBoardWidget(board_card, self._on_move, bg=theme.PANEL_BG)
        self.board_widget.pack(padx=16, pady=16)
        self.board_widget.show_board(self.board, flipped=self.my_color == chess.BLACK)

        side = ctk.CTkFrame(self, fg_color="transparent", width=300)
        side.pack(side="left", fill="y", padx=(10, 20), pady=20)
        theme.label(side, title, "heading", wraplength=280).pack(anchor="w")
        you = "White" if self.my_color == chess.WHITE else "Black"
        theme.label(side, f"You play {you}. Can you convert (or hold) this position?",
                    "muted", wraplength=280).pack(anchor="w", pady=(4, 14))

        theme.label(side, "ENGINE STRENGTH", "section").pack(anchor="w", pady=(0, 4))
        self.strength_var = ctk.StringVar(value="Intermediate (1700)")
        theme.option_menu(side, list(STRENGTHS), self.strength_var, width=280).pack(anchor="w")

        self.status_label = theme.label(side, "", "status", wraplength=280)
        self.status_label.pack(anchor="w", pady=(16, 4))
        self.moves_label = theme.label(side, "", "muted", wraplength=280)
        self.moves_label.pack(anchor="w", fill="x")

        buttons = ctk.CTkFrame(side, fg_color="transparent")
        buttons.pack(side="bottom", fill="x")
        theme.button(buttons, "Undo move", self._undo).pack(fill="x", pady=3)
        theme.button(buttons, "Restart position", self._restart).pack(fill="x", pady=3)
        theme.button(buttons, "Close", self._close, "ghost").pack(fill="x", pady=3)

    # ---------- engine thread ----------

    def _engine_loop(self, path: str) -> None:
        try:
            engine = chess.engine.SimpleEngine.popen_uci(path)
        except Exception as e:
            self._results.put(("error", str(e)))
            return
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                fen, elo = job
                options = {}
                if "UCI_LimitStrength" in engine.options:
                    options["UCI_LimitStrength"] = elo is not None
                    if elo is not None and "UCI_Elo" in engine.options:
                        opt = engine.options["UCI_Elo"]
                        options["UCI_Elo"] = max(opt.min or elo, min(opt.max or elo, elo))
                engine.configure(options)
                result = engine.play(chess.Board(fen), chess.engine.Limit(time=THINK_SECONDS))
                self._results.put(("move", fen, result.move))
        except Exception as e:
            self._results.put(("error", str(e)))
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                item = self._results.get_nowait()
                if item[0] == "error":
                    self._thinking = False
                    self._set_status(f"Engine error: {item[1]}", theme.ERROR)
                elif item[0] == "move":
                    _, fen, move = item
                    self._thinking = False
                    # ignore a stale reply (position changed by undo/restart meanwhile)
                    if fen == self.board.fen() and move in self.board.legal_moves:
                        self.board.push(move)
                        self._refresh()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _request_engine_move(self) -> None:
        if self.board.is_game_over():
            return
        self._thinking = True
        self._set_status("Stockfish is thinking...", theme.TEXT_MUTED)
        self._jobs.put((self.board.fen(), STRENGTHS[self.strength_var.get()]))

    # ---------- your moves ----------

    def _on_move(self, move: chess.Move) -> None:
        if self._thinking or self.board.turn != self.my_color or self.board.is_game_over():
            return
        self.board.push(move)
        self._refresh()
        self._request_engine_move()

    def _undo(self) -> None:
        if self._thinking:
            return
        start_len = len(chess.Board(self.start_fen).move_stack)
        # take back to the last position where it's your move
        while len(self.board.move_stack) > start_len:
            self.board.pop()
            if self.board.turn == self.my_color:
                break
        self._refresh()

    def _restart(self) -> None:
        self._thinking = False
        self.board = chess.Board(self.start_fen)
        self.board_widget.show_board(self.board)
        self._refresh()
        if self.board.turn != self.my_color:
            self._request_engine_move()

    def _close(self) -> None:
        self._closed = True
        self._jobs.put(None)
        self.destroy()

    # ---------- display ----------

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, text_color=color)

    def _refresh(self) -> None:
        self.board_widget.show_board(self.board)
        replay = chess.Board(self.start_fen)
        sans = []
        for move in self.board.move_stack[len(replay.move_stack):]:
            prefix = f"{replay.fullmove_number}. " if replay.turn == chess.WHITE else (
                f"{replay.fullmove_number}... " if not sans else "")
            sans.append(prefix + replay.san(move))
            replay.push(move)
        self.moves_label.configure(text=" ".join(sans))

        if self.board.is_game_over():
            outcome = self.board.outcome()
            if outcome.winner is None:
                self._set_status(f"Draw ({outcome.termination.name.replace('_', ' ').lower()}).", theme.TEXT)
            elif outcome.winner == self.my_color:
                self._set_status("You won! Position converted.", theme.SUCCESS)
            else:
                self._set_status("Stockfish won. Try again with Restart.", theme.ERROR)
        elif not self._thinking:
            self._set_status("Your move." if self.board.turn == self.my_color else "", theme.TEXT)
