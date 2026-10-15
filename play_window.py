"""'Play it out' window: play any position against Stockfish at an
adjustable strength — or analyze it yourself, moving both sides, with an
optional engine evaluation. Used from puzzles (and the endgame filter) and
at the end of a drilled opening line.

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
from board_widget import BOARD_PIXELS, REPLY_COLOR, ChessBoardWidget

# label -> UCI_Elo (None = full strength). Stockfish's minimum Elo is ~1320.
STRENGTHS = {
    "Club (1400)": 1400,
    "Intermediate (1700)": 1700,
    "Strong (2000)": 2000,
    "Expert (2300)": 2300,
    "Full strength": None,
}
THINK_SECONDS = 0.6
EVAL_SECONDS = 0.5          # per position while "Show engine evaluation" is on
PLAY_MODE = "Play Stockfish"
ANALYZE_MODE = "Analyze myself"


class PlayWindow(ctk.CTkToplevel):
    def __init__(self, master, fen: str, title: str = "Play it out",
                 my_color: Optional[chess.Color] = None, conn=None, analyze: bool = False):
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
        self._engine_ok = False

        self._build(title)
        if analyze:                       # straight into Analyze myself (both sides, engine eval)
            self.mode_switch.set(ANALYZE_MODE)
            self._on_mode_changed()
        self.protocol("WM_DELETE_WINDOW", self._close)

        path = engine_locator.find_stockfish()
        if path is None:
            self._set_status("Stockfish not found — see Puzzle Review for setup help.", theme.ERROR)
        else:
            self._engine_ok = True
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
        self.mode_switch = ctk.CTkSegmentedButton(
            side, values=[PLAY_MODE, ANALYZE_MODE], command=lambda _: self._on_mode_changed(),
            height=32, font=theme.FONT_BOLD, fg_color=theme.PANEL_BG,
            selected_color=theme.CONTROL_BG, selected_hover_color=theme.CONTROL_BG,
            unselected_color=theme.PANEL_BG, unselected_hover_color=theme.PANEL_ALT,
            text_color=theme.TEXT,
        )
        self.mode_switch.set(PLAY_MODE)
        self.mode_switch.pack(anchor="w", fill="x", pady=(8, 0))
        self.intro_label = theme.label(side, "", "muted", wraplength=280)
        self.intro_label.pack(anchor="w", pady=(6, 14))

        # play mode: opponent strength / analyze mode: optional engine evaluation
        self.mode_options = ctk.CTkFrame(side, fg_color="transparent")
        self.mode_options.pack(anchor="w", fill="x")
        self.strength_frame = ctk.CTkFrame(self.mode_options, fg_color="transparent")
        theme.label(self.strength_frame, "ENGINE STRENGTH", "section").pack(anchor="w", pady=(0, 4))
        self.strength_var = ctk.StringVar(value="Intermediate (1700)")
        theme.option_menu(self.strength_frame, list(STRENGTHS), self.strength_var, width=280).pack(anchor="w")
        self.eval_var = ctk.BooleanVar(value=False)
        self.eval_box = ctk.CTkCheckBox(
            self.mode_options, text="Show engine evaluation", variable=self.eval_var,
            command=self._refresh, font=theme.FONT_BASE, text_color=theme.TEXT_SOFT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER_STRONG,
            checkbox_width=18, checkbox_height=18,
        )
        self._show_mode_options()

        self.status_label = theme.label(side, "", "status", wraplength=280)
        self.status_label.pack(anchor="w", pady=(16, 4))
        self.eval_label = theme.label(side, "", "body", wraplength=280)
        self.eval_label.pack(anchor="w", fill="x")
        self.moves_label = theme.label(side, "", "muted", wraplength=280)
        self.moves_label.pack(anchor="w", fill="x", pady=(4, 0))

        buttons = ctk.CTkFrame(side, fg_color="transparent")
        buttons.pack(side="bottom", fill="x")
        theme.button(buttons, "Undo move", self._undo).pack(fill="x", pady=3)
        theme.button(buttons, "Restart position", self._restart).pack(fill="x", pady=3)
        theme.button(buttons, "Flip board", self._flip).pack(fill="x", pady=3)
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
                if job[0] == "eval":
                    if not self._jobs.empty():
                        continue        # you've moved on already — only the newest position matters
                    _, fen = job
                    if "UCI_LimitStrength" in engine.options:
                        engine.configure({"UCI_LimitStrength": False})
                    info = engine.analyse(chess.Board(fen), chess.engine.Limit(time=EVAL_SECONDS))
                    pv = info.get("pv") or []
                    self._results.put(("eval", fen, info["score"], pv[0] if pv else None))
                    continue
                _, fen, elo = job
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
                elif item[0] == "eval":
                    _, fen, score, best = item
                    if fen == self.board.fen() and self._evaluating():
                        self._show_eval(score, best)
                elif item[0] == "move":
                    _, fen, move = item
                    self._thinking = False
                    # ignore a stale reply (position changed by undo/restart meanwhile,
                    # or you switched to analyzing while it was thinking)
                    if fen == self.board.fen() and move in self.board.legal_moves and not self._analyzing():
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
        self._jobs.put(("move", self.board.fen(), STRENGTHS[self.strength_var.get()]))

    # ---------- your moves ----------

    def _analyzing(self) -> bool:
        return self.mode_switch.get() == ANALYZE_MODE

    def _evaluating(self) -> bool:
        return self._analyzing() and self.eval_var.get() and self._engine_ok

    def _on_mode_changed(self) -> None:
        self._show_mode_options()
        self._refresh()
        # back to playing on Stockfish's turn: it moves
        if not self._analyzing() and not self._thinking and self.board.turn != self.my_color:
            self._request_engine_move()

    def _show_mode_options(self) -> None:
        you = "White" if self.my_color == chess.WHITE else "Black"
        if self._analyzing():
            self.strength_frame.pack_forget()
            self.eval_box.pack(anchor="w")
            self.intro_label.configure(text="Move both sides yourself and try ideas out. "
                                            "Undo takes back one move.")
        else:
            self.eval_box.pack_forget()
            self.strength_frame.pack(anchor="w", fill="x")
            self.intro_label.configure(text=f"You play {you}. Can you convert (or hold) this position?")

    def _on_move(self, move: chess.Move) -> None:
        if self._thinking or self.board.is_game_over():
            return
        if not self._analyzing() and self.board.turn != self.my_color:
            return
        self.board.push(move)
        self._refresh()
        if not self._analyzing():
            self._request_engine_move()

    def _flip(self) -> None:
        self.board_widget.show_board(self.board, flipped=not self.board_widget.flipped)
        self._refresh()

    def _undo(self) -> None:
        if self._thinking:
            return
        if self._analyzing():
            if len(self.board.move_stack) > len(chess.Board(self.start_fen).move_stack):
                self.board.pop()
            self._refresh()
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
        if not self._analyzing() and self.board.turn != self.my_color:
            self._request_engine_move()

    def _close(self) -> None:
        self._closed = True
        self._jobs.put(None)
        self.destroy()

    # ---------- display ----------

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, text_color=color)

    def _show_eval(self, score: chess.engine.PovScore, best: Optional[chess.Move]) -> None:
        white = score.white()
        if white.is_mate():
            mate = white.mate()
            text = f"Mate in {abs(mate)} for {'White' if mate > 0 else 'Black'}" if mate else "Checkmate"
        else:
            text = f"{white.score() / 100:+.1f}"
        if best is not None and best in self.board.legal_moves:
            text += f" · best: {self.board.san(best)}"
            self.board_widget.set_marked_arrows([(best.from_square, best.to_square, REPLY_COLOR)])
        self.eval_label.configure(text=f"Evaluation {text}", text_color=theme.TEXT)

    def _refresh(self) -> None:
        self.board_widget.show_board(self.board)
        self.eval_label.configure(text="")
        if self._evaluating() and not self.board.is_game_over():
            self.eval_label.configure(text="Evaluating…", text_color=theme.TEXT_MUTED)
            self._jobs.put(("eval", self.board.fen()))
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
            elif self._analyzing():
                self._set_status(f"Checkmate — {'White' if outcome.winner else 'Black'} wins.", theme.TEXT)
            elif outcome.winner == self.my_color:
                self._set_status("You won! Position converted.", theme.SUCCESS)
            else:
                self._set_status("Stockfish won. Try again with Restart.", theme.ERROR)
        elif self._analyzing():
            self._set_status(f"{'White' if self.board.turn == chess.WHITE else 'Black'} to move.", theme.TEXT)
        elif not self._thinking:
            self._set_status("Your move." if self.board.turn == self.my_color else "", theme.TEXT)
