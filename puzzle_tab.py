"""Puzzle review tab: import games, analyze with Stockfish, browse puzzles
freely via a side queue (no spaced-repetition gating on which ones show).

Stockfish output is cached per game (see analyzer.py), so each game only
ever goes through the engine once; changing the blunder size afterwards
just re-filters the cache and is instant."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

import customtkinter as ctk
from typing import List, Optional

import chess

import analyzer
import chess_com_client
import db
import chess.engine

import engine_locator
import explain
import stats
import style
import theme
from play_window import PlayWindow
from board_widget import BOARD_PIXELS, GAME_MOVE_COLOR, REPLY_COLOR, ChessBoardWidget
from models import TIME_FILTERS, Game, Puzzle

TYPE_COLORS = {
    "Missed Mate": theme.ERROR,
    "Hanging Piece": theme.WARNING,
    "Blunder": theme.ERROR,
    "Mistake": theme.INFO,
    "Inaccuracy": theme.TEXT_MUTED,
}

WEAK_SPOTS = "Weak spots"
ALL_POSITIONS = "All positions"
SHARP_ONLY = "Sharp (one best move)"
QUIET_ONLY = "Quiet (several good moves)"
ENDGAMES = "Endgames"

DEFAULT_THRESHOLD_PAWNS = "1.5"
DEFAULT_GAMES_PER_RUN = "50"
DEFAULT_IMPORT_MONTHS = "12"

# widget classes that use the arrow keys themselves — don't steal them
_TEXT_INPUT_CLASSES = {"Entry", "TEntry", "Spinbox", "TSpinbox", "TCombobox", "Text", "Listbox"}


def _cp_to_pawns(cp: Optional[int]) -> str:
    """Formats a centipawn value as a signed pawns figure, e.g. '+1.5'.
    Mate scores (clamped to +/-MATE_CP by the analyzer) read as 'mate'."""
    if cp is None:
        return "?"
    if cp >= analyzer.MATE_CP:
        return "a forced mate for you"
    if cp <= -analyzer.MATE_CP:
        return "a forced mate against you"
    pawns = cp / 100
    sign = "+" if pawns >= 0 else ""
    return f"{sign}{pawns:.1f}"


def _swing_text(p: Puzzle) -> str:
    """Short label for how costly the mistake was, e.g. '-1.5' or 'allowed mate'."""
    if p.eval_after_cp is not None and p.eval_after_cp <= -analyzer.MATE_CP:
        return "allowed mate"
    if p.eval_swing_cp is None:
        return "?"
    return f"−{p.eval_swing_cp / 100:.1f}"


class PuzzleReviewTab(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        # on_review_game(game_id, fen): open Game Review at that position
        self.on_review_game = on_review_game
        self._attempt_recorded = False
        self.queue: List[Puzzle] = []
        self.queue_index: int = -1
        self.current_puzzle: Optional[Puzzle] = None
        self.current_game: Optional[Game] = None
        # "solving" -> accepting moves; "wrong" -> waiting for retry/reveal;
        # "done" -> solved or revealed, waiting for Next
        self.attempt_state: str = "solving"
        self.solved_ids: set[str] = set()   # solved on the first try this session

        # Background threads (import/analyze) must never call Tk methods
        # directly — not even self.after(), which itself isn't safe to
        # call from a non-main thread on every platform. Instead, worker
        # threads push callables here, and a timer already running on the
        # main thread drains it.
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._stop_event = threading.Event()
        self._busy = False

        self.import_page = ctk.CTkFrame(master, fg_color="transparent")
        self._build_layout()
        self._refresh_game_summary()
        self._reload_queue()
        self.after(100, self._poll_ui_queue)

        top = self.winfo_toplevel()
        top.bind("<Left>", lambda e: self._on_arrow_key(self._go_previous), add="+")
        top.bind("<Right>", lambda e: self._on_arrow_key(self._go_next), add="+")

    def _poll_ui_queue(self) -> None:
        if not self.winfo_exists():
            return   # tab destroyed (player switched)
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    # ---------- Layout ----------

    def _build_layout(self) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        board_pad = ctk.CTkFrame(left, fg_color="transparent")
        board_pad.pack(padx=18, pady=16)

        header = ctk.CTkFrame(board_pad, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        theme.button(header, "Play it out", self._play_it_out, height=28, width=96).pack(side="right")
        theme.button(header, "Review game", self._review_game, height=28, width=104).pack(
            side="right", padx=(0, 6))
        self.game_label = theme.label(header, "", "muted", wraplength=300)
        self.game_label.pack(side="left", fill="x")

        self.board_widget = ChessBoardWidget(board_pad, self._on_move_attempt, bg=theme.PANEL_BG)
        self.board_widget.pack()

        self.status_label = theme.label(board_pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(pady=(12, 0), anchor="w")
        self.explanation_label = theme.label(board_pad, "", "body", wraplength=BOARD_PIXELS)
        self.explanation_label.pack(pady=(2, 0), anchor="w")

        button_row = ctk.CTkFrame(board_pad, fg_color="transparent")
        button_row.pack(pady=(12, 0), fill="x")
        self.prev_button = theme.button(button_row, "← Previous", self._go_previous, width=112)
        self.prev_button.pack(side="left")
        self.retry_button = theme.button(button_row, "Try again", self._on_retry, width=100)
        self.reveal_button = theme.button(button_row, "Show answer", self._on_reveal, width=120)
        # retry/reveal are packed only after a wrong attempt
        self.next_button = theme.button(button_row, "Next puzzle →", self._go_next, "primary", width=140)
        self.next_button.pack(side="right")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)

        # ---- Games card ----
        theme.label(self.import_page, "Import & Analyze", "title").pack(anchor="w", padx=24, pady=(20, 4))
        theme.label(self.import_page, "Download games from chess.com, then let Stockfish find your "
                                      "mistakes. They become puzzles in Puzzle Review.", "muted").pack(
            anchor="w", padx=24, pady=(0, 12))
        games_card = theme.card(self.import_page)
        games_card.pack(anchor="nw", padx=24)
        games = ctk.CTkFrame(games_card, fg_color="transparent")
        games.pack(fill="x", padx=18, pady=16)

        heading_row = ctk.CTkFrame(games, fg_color="transparent")
        heading_row.pack(fill="x")
        theme.label(heading_row, "Your games", "heading").pack(side="left")
        self.focus_var = tk.StringVar(value=db.get_setting(self.conn, "focus_filter") or "All games")
        if self.focus_var.get() not in TIME_FILTERS:
            self.focus_var.set("All games")
        theme.option_menu(heading_row, list(TIME_FILTERS), self.focus_var,
                          lambda _: self._on_focus_changed(), width=150).pack(side="right")
        theme.label(heading_row, "Focus", "muted").pack(side="right", padx=(0, 8))

        self.games_summary_label = theme.label(games, "", "muted", wraplength=340)
        self.games_summary_label.pack(anchor="w", fill="x", pady=(6, 10))

        self.import_button = theme.button(games, "Import games from chess.com", self._import_games)
        self.import_button.pack(fill="x")
        months_row = ctk.CTkFrame(games, fg_color="transparent")
        months_row.pack(fill="x", pady=(6, 0))
        theme.label(months_row, "Import last", "muted").pack(side="left")
        self.import_months_var = tk.StringVar(
            value=db.get_setting(self.conn, "import_months") or DEFAULT_IMPORT_MONTHS
        )
        theme.entry(months_row, self.import_months_var, width=52).pack(side="left", padx=8)
        theme.label(months_row, "months (0 = all history)", "muted").pack(side="left")

        theme.label(games, "FIND MISTAKES", "section").pack(anchor="w", pady=(16, 6))
        settings = ctk.CTkFrame(games, fg_color="transparent")
        settings.pack(fill="x")
        self.threshold_var = tk.StringVar(
            value=db.get_setting(self.conn, "threshold_pawns") or DEFAULT_THRESHOLD_PAWNS
        )
        self.games_per_run_var = tk.StringVar(
            value=db.get_setting(self.conn, "games_per_run") or DEFAULT_GAMES_PER_RUN
        )
        for row, (text, var, hint) in enumerate((
            ("Blunder size", self.threshold_var, "pawns lost"),
            ("Games per run", self.games_per_run_var, "newest first"),
        )):
            theme.label(settings, text).grid(row=row, column=0, sticky="w", pady=3)
            theme.entry(settings, var, width=64).grid(row=row, column=1, sticky="w", padx=10, pady=3)
            theme.label(settings, hint, "muted").grid(row=row, column=2, sticky="w", pady=3)

        self.analyze_button = theme.button(games, "Analyze new games", self._analyze_games, "primary")
        self.analyze_button.pack(fill="x", pady=(12, 6))

        self.stop_button = theme.button(games, "Stop (keeps finished games)", self._on_stop)
        self.progress_bar = theme.progress_bar(games)
        self.progress_label = theme.label(games, "", "muted")
        self._progress_max: Optional[int] = None
        # stop/progress are shown only while work is running
        self._progress_anchor = ctk.CTkFrame(games, height=0, fg_color="transparent")
        self._progress_anchor.pack(fill="x")

        self.regenerate_button = theme.button(games, "Apply blunder size (instant)", self._regenerate_puzzles)
        self.regenerate_button.pack(fill="x")
        theme.label(
            games, "Stockfish results are saved, so games are never analyzed twice.",
            "muted", wraplength=340,
        ).pack(anchor="w", pady=(6, 0))

        # ---- Puzzle queue card ----
        queue_card = theme.card(right)
        queue_card.pack(fill="both", expand=True)
        queue_box = ctk.CTkFrame(queue_card, fg_color="transparent")
        queue_box.pack(fill="both", expand=True, padx=18, pady=16)

        queue_heading = ctk.CTkFrame(queue_box, fg_color="transparent")
        queue_heading.pack(fill="x", pady=(0, 8))
        theme.label(queue_heading, "Puzzle queue", "heading").pack(side="left")
        self.type_filter_var = tk.StringVar(value="All types")
        self.type_filter_dropdown = theme.option_menu(
            queue_heading, ["All types"], self.type_filter_var, lambda _: self._reload_queue(), width=150,
        )
        self.type_filter_dropdown.pack(side="right")

        position_row = ctk.CTkFrame(queue_box, fg_color="transparent")
        position_row.pack(fill="x", pady=(0, 8))
        theme.label(position_row, "Position", "muted").pack(side="left")
        self.position_var = tk.StringVar(value=ALL_POSITIONS)
        theme.option_menu(position_row, [ALL_POSITIONS, SHARP_ONLY, QUIET_ONLY], self.position_var,
                          lambda _: self._reload_queue(), width=230).pack(side="right")

        # the same Focus setting as on Import & Analyze (shared variable = always in sync)
        focus_row = ctk.CTkFrame(queue_box, fg_color="transparent")
        focus_row.pack(fill="x", pady=(0, 8))
        theme.label(focus_row, "Games", "muted").pack(side="left")
        theme.option_menu(focus_row, list(TIME_FILTERS), self.focus_var,
                          lambda _: self._on_focus_changed(), width=230).pack(side="right")

        # packed before the list (at the bottom) so it's never clipped
        self.queue_count_label = theme.label(queue_box, "", "muted")
        self.queue_count_label.pack(side="bottom", anchor="w", pady=(8, 0))

        list_frame = ctk.CTkFrame(queue_box, fg_color=theme.PANEL_ALT, corner_radius=10)
        list_frame.pack(fill="both", expand=True)
        self.queue_listbox = theme.listbox(list_frame, height=5, width=30)
        scrollbar = ctk.CTkScrollbar(list_frame, command=self.queue_listbox.yview,
                                     button_color=theme.CONTROL_BG, fg_color=theme.PANEL_ALT)
        self.queue_listbox.configure(yscrollcommand=scrollbar.set)
        self.queue_listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scrollbar.pack(side="left", fill="y", pady=8)
        self.queue_listbox.bind("<<ListboxSelect>>", self._on_queue_select)

    def _focus_classes(self) -> Optional[set]:
        """Time classes the Focus dropdown limits analysis and the queue to."""
        return TIME_FILTERS[self.focus_var.get()]

    def _on_focus_changed(self) -> None:
        db.set_setting(self.conn, "focus_filter", self.focus_var.get())
        self._refresh_game_summary()
        self._reload_queue()

    def _refresh_game_summary(self) -> None:
        s = db.game_stats(self.conn, self._focus_classes())
        if s["total"] == 0:
            text = "No games yet. Import from chess.com to get started."
        else:
            parts = [f"{s['total']} games imported", f"{s['cached'] + s['legacy']} analyzed"]
            if s["new"]:
                parts.append(f"{s['new']} waiting")
            text = " · ".join(parts)
            if s["legacy"]:
                text += (f"\n{s['legacy']} were analyzed in an older format and need a one-time "
                         "re-analysis for full data (top 3 engine moves). They keep their puzzles; "
                         "Analyze new games handles them after new games.")
        self.games_summary_label.configure(text=text)

    # ---------- Queue management ----------

    def _reload_queue(self) -> None:
        """Rebuilds the queue from the database — call after analyzing new
        games or changing the type filter. Randomly ordered, no due-date
        gating: every generated puzzle is browsable."""
        types = db.distinct_puzzle_types(self.conn)
        options = ["All types", WEAK_SPOTS, ENDGAMES] + types
        self.type_filter_dropdown.configure(values=options)
        if self.type_filter_var.get() not in options:
            self.type_filter_var.set("All types")

        selected_type = self.type_filter_var.get()
        filter_type = None if selected_type in ("All types", WEAK_SPOTS, ENDGAMES) else selected_type
        self.queue = db.all_puzzles(self.conn, puzzle_type=filter_type,
                                    time_classes=self._focus_classes())
        if selected_type == WEAK_SPOTS:
            # puzzles you keep missing, weakest first (solve twice in a row to clear one)
            weak = db.weak_item_ids(self.conn, "puzzle")
            self.queue = sorted((p for p in self.queue if p.id in weak), key=lambda p: -weak[p.id])
        elif selected_type == ENDGAMES:
            self.queue = [p for p in self.queue if stats.is_endgame(p.fen)]
        position = self.position_var.get()
        if position != ALL_POSITIONS:
            # sharp = one clearly best move (engine's top choice far ahead of its 2nd)
            sharp = style.puzzle_sharpness(self.conn, self.queue)
            self.queue = [p for p in self.queue if sharp.get(p.id) == (position == SHARP_ONLY)]
        empty_text = {
            WEAK_SPOTS: "No weak spots right now. Puzzles you miss show up here until you "
                        "solve them twice in a row.",
            ENDGAMES: "No endgame puzzles in this filter yet.",
        }.get(selected_type, "No puzzles match these filters." if position != ALL_POSITIONS else
               "No puzzles yet. Import your games, then click Analyze new games.")

        self._refresh_listbox()
        if self.queue:
            self._load_puzzle_at(0)
        else:
            self.queue_index = -1
            self.current_puzzle = None
            self.current_game = None
            self.board_widget.clear()
            self.game_label.configure(text="")
            self.status_label.configure(text=empty_text, text_color=theme.TEXT_MUTED)
            self.explanation_label.configure(text="")
            self._hide_retry_buttons()

    def _listbox_label(self, index: int) -> str:
        p = self.queue[index]
        check = "✓ " if p.id in self.solved_ids else "   "
        return f"{check}{index + 1}.  {p.puzzle_type or 'Puzzle'}  ({_swing_text(p)})"

    def _refresh_listbox(self) -> None:
        self.queue_listbox.delete(0, tk.END)
        for i, p in enumerate(self.queue):
            self.queue_listbox.insert(tk.END, self._listbox_label(i))
            self.queue_listbox.itemconfig(i, foreground=TYPE_COLORS.get(p.puzzle_type, theme.TEXT))
        self._update_count_label()

    def _update_count_label(self) -> None:
        solved = sum(1 for p in self.queue if p.id in self.solved_ids)
        text = f"{len(self.queue)} puzzle(s)"
        if solved:
            text += f" · {solved} solved this session"
        self.queue_count_label.configure(text=text)

    def _refresh_listbox_row(self, index: int) -> None:
        self.queue_listbox.delete(index)
        self.queue_listbox.insert(index, self._listbox_label(index))
        p = self.queue[index]
        self.queue_listbox.itemconfig(index, foreground=TYPE_COLORS.get(p.puzzle_type, theme.TEXT))
        self.queue_listbox.selection_set(index)
        self._update_count_label()

    def _on_queue_select(self, event) -> None:
        selection = self.queue_listbox.curselection()
        if selection and selection[0] != self.queue_index:
            self._load_puzzle_at(selection[0])

    def _on_arrow_key(self, action) -> None:
        if not self.winfo_ismapped():
            return  # another tab is showing
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in _TEXT_INPUT_CLASSES and focused is not self.queue_listbox:
            return
        action()

    def _go_next(self) -> None:
        if not self.queue:
            return
        self._load_puzzle_at((self.queue_index + 1) % len(self.queue))

    def _go_previous(self) -> None:
        if not self.queue:
            return
        self._load_puzzle_at((self.queue_index - 1) % len(self.queue))

    def _load_puzzle_at(self, index: int) -> None:
        self.attempt_state = "solving"
        self._hide_retry_buttons()
        self.explanation_label.configure(text="")
        self.queue_index = index
        self.current_puzzle = self.queue[index]
        self._attempt_recorded = False
        self._explain_token = None
        self.current_game = (
            db.get_game(self.conn, self.current_puzzle.source_game_id)
            if self.current_puzzle.source_game_id else None
        )
        board = chess.Board(self.current_puzzle.fen)
        # puzzle positions are always your move — show the board from your side
        self.board_widget.load_fen(self.current_puzzle.fen, flipped=board.turn == chess.BLACK)
        self.game_label.configure(text=self._game_label_text())
        self._show_prompt()
        self._show_game_move()

        self.queue_listbox.selection_clear(0, tk.END)
        self.queue_listbox.selection_set(index)
        self.queue_listbox.see(index)

    def _show_prompt(self) -> None:
        side = "White" if chess.Board(self.current_puzzle.fen).turn == chess.WHITE else "Black"
        self.status_label.configure(text=f"Find the best move for {side}.", text_color=theme.TEXT)

    def _game_label_text(self) -> str:
        if self.current_game is None:
            return ""
        my_username = db.get_setting(self.conn, "chess_com_username") or "You"
        if self.current_game.my_color.value == "white":
            white, black = my_username, self.current_game.opponent_username
        else:
            white, black = self.current_game.opponent_username, my_username
        played = self.current_game.played_at.strftime("%b %d, %Y")
        type_tag = f"{self.current_puzzle.puzzle_type} — " if self.current_puzzle.puzzle_type else ""
        return f"{type_tag}{white} (White) vs {black} (Black) — {played}"

    # ---------- Attempting a puzzle ----------

    def _on_move_attempt(self, move: chess.Move) -> None:
        if self.current_puzzle is None or self.attempt_state != "solving":
            return

        board = self.board_widget.board
        played_san = board.san(move)
        correct = move.uci() == self.current_puzzle.correct_move_uci

        board.push(move)
        self.board_widget.redraw()
        if not self._attempt_recorded:
            # only the first try counts toward Weak spots / Progress
            db.record_attempt(self.conn, "puzzle", self.current_puzzle.id, correct)
            self._attempt_recorded = True

        if correct:
            self.attempt_state = "done"
            self.solved_ids.add(self.current_puzzle.id)
            self._refresh_listbox_row(self.queue_index)
            self.status_label.configure(text=f"Correct — {played_san}!", text_color=theme.SUCCESS)
            self.board_widget.set_marked_arrows([])
            self.explanation_label.configure(text=self._explanation_text(correct=True))
            self._explain_solution_async()
        else:
            self.attempt_state = "wrong"
            self.status_label.configure(
                text=f"Not quite — {played_san} isn't the best move here.",
                text_color=theme.ERROR,
            )
            self._show_retry_buttons()
            fen = self.current_puzzle.fen
            self.explanation_label.configure(text="Working out why…")
            self.board_widget.set_marked_arrows([])
            self._explain_async(lambda engine: self._attempt_job(fen, move, engine))

    # ---------- move explanations (Stockfish lines + board facts) ----------

    def _explain_solution_async(self) -> None:
        p = self.current_puzzle
        best = chess.Move.from_uci(p.correct_move_uci)
        try:
            game_move = chess.Board(p.fen).parse_san(p.played_move_san) if p.played_move_san else None
        except ValueError:
            game_move = None
        summary = self._eval_summary()
        self.explanation_label.configure(text="Working out the key line…")
        self._explain_async(lambda engine: explain.explain_solution(p.fen, best, game_move, engine)
                            + (f"\n{summary}" if summary else ""))

    def _explain_async(self, job) -> None:
        """Runs job(engine) on a background thread with a short-lived
        Stockfish (None if not installed) and shows the text when done —
        unless the user has moved on to another puzzle or retried."""
        token = object()
        self._explain_token = token

        def worker():
            engine = None
            try:
                path = engine_locator.find_stockfish()
                if path:
                    engine = chess.engine.SimpleEngine.popen_uci(path)
                text = job(engine)
            except Exception:
                text = None
            finally:
                if engine is not None:
                    try:
                        engine.quit()
                    except Exception:
                        pass

            def apply():
                if self._explain_token is not token or not text:
                    return
                if isinstance(text, tuple):          # (explanation, arrows to draw)
                    words, arrows = text
                    self.explanation_label.configure(text=words)
                    if self.attempt_state == "wrong":
                        self.board_widget.set_marked_arrows(arrows)
                else:
                    self.explanation_label.configure(text=text)
            self._ui_queue.put(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _attempt_job(self, fen: str, move: chess.Move, engine):
        """Explanation of a wrong try + the computer's reply as a green arrow."""
        text, line = explain.explain_attempt(fen, move, engine, with_line=True)
        arrows = [(line[0].from_square, line[0].to_square, REPLY_COLOR)] if line else []
        if line:
            board = chess.Board(fen)
            board.push(move)
            text += f"\nGreen arrow: the computer's reply {board.san(line[0])}."
        return text, arrows

    def _show_game_move(self) -> None:
        """Blue arrow + note for the move you actually played in the game."""
        p = self.current_puzzle
        if p is None or not p.played_move_san:
            return
        try:
            move = chess.Board(p.fen).parse_san(p.played_move_san)
        except ValueError:
            return
        self.board_widget.set_marked_arrows([(move.from_square, move.to_square, GAME_MOVE_COLOR)])
        self.explanation_label.configure(
            text=f"In your game you played {p.played_move_san} (blue arrow). Find something better.")

    def _eval_summary(self) -> str:
        p = self.current_puzzle
        if p.eval_before_cp is None:
            return ""
        return (f"Evaluation: {_cp_to_pawns(p.eval_before_cp)} with the best move, "
                f"{_cp_to_pawns(p.eval_after_cp)} after {p.played_move_san} in your game.")

    def _play_it_out(self) -> None:
        """Play the puzzle position against Stockfish — for endgames this
        is the endgame trainer: can you actually convert it?"""
        if self.current_puzzle is None:
            return
        PlayWindow(self, self.current_puzzle.fen,
                   title=f"Play it out — {self.current_puzzle.puzzle_type or 'puzzle'}")

    def _review_game(self) -> None:
        if self.current_puzzle is None or not self.current_puzzle.source_game_id:
            return
        if self.on_review_game:
            self.on_review_game(self.current_puzzle.source_game_id, self.current_puzzle.fen)

    def _on_retry(self) -> None:
        if self.current_puzzle is None:
            return
        self.attempt_state = "solving"
        self._explain_token = None
        self.explanation_label.configure(text="")
        self._hide_retry_buttons()
        self.board_widget.load_fen(self.current_puzzle.fen)
        self._show_prompt()
        self._show_game_move()

    def _on_reveal(self) -> None:
        if self.current_puzzle is None:
            return
        self.attempt_state = "done"
        if not self._attempt_recorded:   # revealed without trying = a miss
            db.record_attempt(self.conn, "puzzle", self.current_puzzle.id, False)
            self._attempt_recorded = True
        self._hide_retry_buttons()
        self.board_widget.load_fen(self.current_puzzle.fen)
        self.board_widget.board.push(chess.Move.from_uci(self.current_puzzle.correct_move_uci))
        self.board_widget.redraw()
        self.status_label.configure(
            text=f"The best move was {self.current_puzzle.correct_move_san}.", text_color=theme.TEXT,
        )
        self.explanation_label.configure(text=self._explanation_text(correct=False))
        self._explain_solution_async()

    def _show_retry_buttons(self) -> None:
        self.retry_button.pack(side="left", padx=(8, 0))
        self.reveal_button.pack(side="left", padx=(8, 0))

    def _hide_retry_buttons(self) -> None:
        self.retry_button.pack_forget()
        self.reveal_button.pack_forget()

    def _explanation_text(self, correct: bool) -> str:
        p = self.current_puzzle
        before = _cp_to_pawns(p.eval_before_cp)
        after = _cp_to_pawns(p.eval_after_cp)
        swing_pawns = f"{p.eval_swing_cp / 100:.1f}" if p.eval_swing_cp is not None else "?"
        allowed_mate = p.eval_after_cp is not None and p.eval_after_cp <= -analyzer.MATE_CP
        # a pawn count is meaningless once mate is on the board
        swing_note = "" if allowed_mate else f" — about a {swing_pawns}-pawn swing"

        if correct:
            return (
                f"This was the engine's top choice here, keeping the evaluation "
                f"around {before}. In your actual game, you played "
                f"{p.played_move_san} instead, which dropped it to {after}{swing_note}."
            )
        else:
            return (
                f"{p.correct_move_san} keeps the evaluation around {before}. "
                f"In your actual game here, you played {p.played_move_san}, "
                f"which dropped the evaluation to {after}{swing_note}. "
                f"That's the mistake this puzzle is testing you on."
            )

    # ---------- Busy state / progress ----------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.import_button, self.analyze_button, self.regenerate_button):
            button.configure(state=state)

    def _show_progress(self, text: str, maximum: Optional[int] = None, stoppable: bool = False) -> None:
        self._progress_max = maximum
        if maximum is None:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_bar.set(0)
        if stoppable:
            self.stop_button.configure(state="normal")
            self.stop_button.pack(after=self._progress_anchor, fill="x", pady=(0, 6))
        self.progress_bar.pack(after=self._progress_anchor, fill="x", pady=(4, 4))
        self.progress_label.pack(after=self.progress_bar, anchor="w", pady=(0, 6))
        self.progress_label.configure(text=text)

    def _update_progress(self, value: int, text: str) -> None:
        if self._progress_max:
            self.progress_bar.set(value / self._progress_max)
        self.progress_label.configure(text=text)

    def _hide_progress(self) -> None:
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.progress_label.pack_forget()
        self.stop_button.pack_forget()

    # ---------- Import ----------

    def _import_games(self) -> None:
        if self._busy:
            return
        existing = db.get_setting(self.conn, "chess_com_username")
        username = simpledialog.askstring(
            "Import games", "Your chess.com username:",
            initialvalue=existing or "", parent=self,
        )
        if not username:
            return
        username = username.strip()
        try:
            months = int(self.import_months_var.get())
            if months < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "Months must be a whole number (0 = all history).",
                                 parent=self)
            return
        db.set_setting(self.conn, "chess_com_username", username)
        db.set_setting(self.conn, "import_months", str(months))

        self._set_busy(True)
        self._show_progress(f"Downloading games for {username}...")

        def on_progress(done: int, total: int):
            text = f"Downloading month {done} of {total}..."
            self._ui_queue.put(lambda: self.progress_label.configure(text=text))

        def worker():
            try:
                games = chess_com_client.fetch_all_games(username, months, on_progress)
                added = 0
                for g in games:
                    if not db.game_exists(self.conn, g.chess_com_url):
                        db.save_game(self.conn, g)
                        added += 1
                self._ui_queue.put(lambda: self._on_import_done(added))
            except Exception as e:
                msg = str(e)
                self._ui_queue.put(lambda: self._on_import_error(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_import_done(self, added: int) -> None:
        self._hide_progress()
        self._set_busy(False)
        self._refresh_game_summary()
        if added == 0:
            messagebox.showinfo("Import complete", "No new games found. You're up to date.", parent=self)
            return
        if messagebox.askyesno(
            "Import complete", f"Imported {added} new games.\n\nAnalyze them now?", parent=self,
        ):
            self._analyze_games()

    def _on_import_error(self, msg: str) -> None:
        self._hide_progress()
        self._set_busy(False)
        messagebox.showerror(
            "Import failed",
            f"Couldn't download games from chess.com:\n\n{msg}\n\n"
            "Check the username and your internet connection.",
            parent=self,
        )

    # ---------- Analyze / regenerate ----------

    def _read_threshold_cp(self) -> Optional[int]:
        try:
            threshold_pawns = float(self.threshold_var.get())
            if threshold_pawns <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "Blunder size must be a positive number, e.g. 1.5",
                                 parent=self)
            return None
        db.set_setting(self.conn, "threshold_pawns", f"{threshold_pawns:.1f}")
        return round(threshold_pawns * 100)

    def _read_games_per_run(self) -> Optional[int]:
        try:
            limit = int(self.games_per_run_var.get())
            if limit <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid value", "Games per run must be a whole number, e.g. 50",
                                 parent=self)
            return None
        db.set_setting(self.conn, "games_per_run", str(limit))
        return limit

    def _analyze_games(self) -> None:
        if self._busy:
            return
        stockfish_path = engine_locator.find_stockfish()
        if stockfish_path is None:
            self._show_stockfish_missing_error()
            return
        threshold_cp = self._read_threshold_cp()
        limit = self._read_games_per_run()
        if threshold_cp is None or limit is None:
            return

        games = db.games_needing_evaluation(self.conn, limit, self._focus_classes())
        if not games:
            scope = "" if self._focus_classes() is None else f" ({self.focus_var.get()})"
            messagebox.showinfo("Nothing to analyze",
                                f"Every imported game{scope} has already been analyzed.", parent=self)
            return

        self._stop_event.clear()
        self._set_busy(True)
        self._show_progress("Preparing...", maximum=1, stoppable=True)

        def worker():
            try:
                self._run_analysis(games, stockfish_path, threshold_cp)
            except Exception as e:
                msg = str(e)
                self._ui_queue.put(lambda: self._on_analysis_error(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _run_analysis(self, games: List[Game], stockfish_path: str, threshold_cp: int) -> None:
        """Runs on a background thread. Each game's engine output is saved
        (and its puzzles generated) the moment it finishes, so stopping or
        closing the app mid-run loses at most the game in progress. UI
        updates go through the queue — never touch Tk from here."""
        total_moves = sum(analyzer.count_my_moves(g) for g in games)
        game_count = len(games)
        progress = {"moves": 0, "games": 0, "puzzles": 0}

        def progress_text() -> str:
            return (f"Game {min(progress['games'] + 1, game_count)} of {game_count} · "
                    f"{progress['moves']} / {total_moves} moves")

        self._ui_queue.put(lambda: self._show_progress(progress_text(), total_moves, stoppable=True))

        def on_move_analyzed():
            progress["moves"] += 1
            done, text = progress["moves"], progress_text()
            self._ui_queue.put(lambda: self._update_progress(done, text))

        def on_game_done(game: Game, evaluations):
            db.save_game_evaluations(self.conn, game.id, evaluations)
            puzzles = analyzer.puzzles_from_evaluations(evaluations, threshold_cp)
            db.replace_puzzles_for_games(self.conn, [game.id], puzzles)
            progress["games"] += 1
            progress["puzzles"] += len(puzzles)

        stopped = False
        try:
            analyzer.evaluate_games_batch(
                games, stockfish_path, on_game_done,
                on_move_analyzed=on_move_analyzed, should_stop=self._stop_event.is_set,
            )
        except analyzer.AnalysisStopped:
            stopped = True

        finished, puzzle_count = progress["games"], progress["puzzles"]

        def on_done():
            self._hide_progress()
            self._set_busy(False)
            self._refresh_game_summary()
            self._reload_queue()
            prefix = "Stopped. " if stopped else ""
            messagebox.showinfo(
                "Analysis stopped" if stopped else "Analysis complete",
                f"{prefix}Analyzed and saved {finished} of {game_count} games, "
                f"found {puzzle_count} puzzles (blunder size {threshold_cp / 100:.1f} pawns).",
                parent=self,
            )

        self._ui_queue.put(on_done)

    def _on_stop(self) -> None:
        self._stop_event.set()
        self.stop_button.configure(state="disabled")
        self.progress_label.configure(text="Stopping after the current move...")

    def _regenerate_puzzles(self) -> None:
        """Re-filters every cached evaluation at the current blunder size.
        Never touches Stockfish, so it takes a second or two at most."""
        if self._busy:
            return
        threshold_cp = self._read_threshold_cp()
        if threshold_cp is None:
            return

        self._set_busy(True)
        self._show_progress("Rebuilding puzzles from saved analysis...")

        def worker():
            try:
                by_game = db.all_evaluations_by_game(self.conn)
                puzzles = []
                for evaluations in by_game.values():
                    puzzles.extend(analyzer.puzzles_from_evaluations(evaluations, threshold_cp))
                db.replace_puzzles_for_games(self.conn, list(by_game.keys()), puzzles)
                legacy = db.game_stats(self.conn)["uncached"]
                self._ui_queue.put(lambda: self._on_regenerate_done(len(by_game), len(puzzles),
                                                                   legacy, threshold_cp))
            except Exception as e:
                msg = str(e)
                self._ui_queue.put(lambda: self._on_analysis_error(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_regenerate_done(self, game_count: int, puzzle_count: int, legacy: int,
                            threshold_cp: int) -> None:
        self._hide_progress()
        self._set_busy(False)
        self._reload_queue()
        msg = (f"Rebuilt {puzzle_count} puzzles from {game_count} analyzed games "
               f"(blunder size {threshold_cp / 100:.1f} pawns).")
        if legacy:
            msg += (f"\n\n{legacy} older games were analyzed before results were saved, "
                    "so they kept their existing puzzles. Analyze new games will save "
                    "them one run at a time.")
        messagebox.showinfo("Puzzles updated", msg, parent=self)

    def _on_analysis_error(self, msg: str) -> None:
        self._hide_progress()
        self._set_busy(False)
        self._refresh_game_summary()
        self._reload_queue()
        messagebox.showerror(
            "Analysis failed",
            f"Something went wrong while analyzing: {msg}\n\n"
            "This usually means the Stockfish binary couldn't run — "
            "check that the path found is actually a working Stockfish "
            "executable for your OS. Games finished before the error are saved.",
            parent=self,
        )

    def _show_stockfish_missing_error(self) -> None:
        messagebox.showerror(
            "Stockfish not found",
            "Couldn't find a Stockfish binary. Either:\n\n"
            "1. Put the stockfish folder/exe inside your chessprep "
            "project folder (e.g. chessprep/stockfish/stockfish...exe), or\n"
            "2. Set the STOCKFISH_PATH environment variable to its "
            "full path before running the app.",
            parent=self,
        )
