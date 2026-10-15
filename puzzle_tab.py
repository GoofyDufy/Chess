"""Puzzle review tab: import games, analyze with Stockfish, browse puzzles
freely via a side queue (no spaced-repetition gating on which ones show).

Stockfish output is cached per game (see analyzer.py), so each game only
ever goes through the engine once; changing the blunder size afterwards
just re-filters the cache and is instant."""

from __future__ import annotations

import queue
import random
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog

import customtkinter as ctk
from typing import List, Optional

import chess

import analyzer
import chess_com_client
import db
import chess.engine

import engine_locator
import engine_service
import explain
import explorer
import lichess_puzzles
import progression
import puzzle_kind
import puzzle_tags
import tkinter.font as tkfont
import spot
import stats
import style
import theme
from play_window import PlayWindow
from eval_bar import EvalBar
from analysis_board import Analysis, IdeasPanel
from board_widget import BOARD_PIXELS, GAME_MOVE_COLOR, REPLY_COLOR, ChessBoardWidget

from models import TIME_FILTERS, Game, Puzzle, PuzzleSource

TYPE_COLORS = {
    "Missed Mate": theme.ERROR,
    analyzer.LEFT_HANGING: theme.WARNING,
    analyzer.MISSED_SACRIFICE: "#F472B6",      # the !! colour
    "Blunder": theme.ERROR,
    "Mistake": theme.INFO,
    "Inaccuracy": theme.TEXT_MUTED,
}

WEAK_SPOTS = "Weak spots"
NEAR_BEST_CP = 50   # a try within half a pawn of the best move is flagged as near-best
MAIN_LINE_PLIES = 12        # how far the step-through main line goes after your best move
SOLUTION_DEPTH = 16         # Stockfish depth for a puzzle's forcing line (background, ~0.3 s)
REPLY_DELAY_MS = 600        # pause before the opponent's reply in a multi-move puzzle
MAIN_LINE_DEPTH = 18        # Stockfish depth for that line (~0.4 s; a time limit often
                            # stops mid-search and reports only a move or two)
ALL_POSITIONS = "All positions"
SHARP_ONLY = "Sharp (one best move)"
QUIET_ONLY = "Quiet (several good moves)"
ENDGAMES = "Endgames"
ALL_KINDS = "All kinds"
MULTI_STEP = "Multi-step (2+ moves)"
# which puzzles the queue holds: your games' mistakes, Lichess's database, or both
MY_GAMES, LICHESS, BOTH_SOURCES = "My games", "Lichess", "My games + Lichess"
SOURCE_OPTIONS = [MY_GAMES, LICHESS, BOTH_SOURCES]
KIND_OPTIONS = [ALL_KINDS, puzzle_kind.ATTACKING, puzzle_kind.DEFENSIVE, puzzle_kind.COUNTERATTACK,
                puzzle_kind.POSITIONAL, MULTI_STEP]
CARD_CONTENT_WIDTH = 520    # the board card's inside width
TAG_PREFIX = "Tag: "        # Type filter entries for the tags you've added
THEME_PREFIX = "Theme: "    # practising one theme (Progress → Themes): any puzzle with that tag

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


def _line_text(sans: List[str], start: chess.Board) -> str:
    """'24. Qxd5 Nxd5 25. Bb5+' — numbered from the puzzle position."""
    parts, number, white = [], start.fullmove_number, start.turn == chess.WHITE
    for i, san in enumerate(sans):
        if white:
            parts.append(f"{number}. {san}")
        else:
            parts.append(f"{number}... {san}" if i == 0 else san)
            number += 1
        white = not white
    return " ".join(parts)


def _record_short(rec: Optional[dict]) -> str:
    """Queue-row tally of every first try, e.g. '✓2 ◐1 ✗1'."""
    if not rec:
        return ""
    parts = [f"{mark}{rec[key]}" for key, mark in (("correct", "✓"), ("near_best", "◐"), ("missed", "✗"))
             if rec[key]]
    return " ".join(parts)


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
        self._attempt_id: Optional[int] = None   # saved first try, while it can still be upgraded
        # step-through main line once solved / revealed: best move + engine continuation
        self._main_line: List[chess.Move] = []
        self._line_index = 0                      # moves of it shown on the board
        self._line_token = None
        # multi-move puzzles: the forcing line to play (yours + replies, up to
        # spot.MAX_SOLUTION_MOVES of yours), worked out in the background on load
        self._solution: List[chess.Move] = []
        self._solution_ready = False
        self._solution_token = None
        self._ply = 0                               # index in _solution of your next move
        self._seq_board: Optional[chess.Board] = None
        self._seq_missed = False                    # a miss anywhere: the puzzle isn't solved clean
        self._run_attempt_id: Optional[int] = None  # the saved first try, for the whole sequence
        self._pays_xp = False
        # puzzle id -> {'correct', 'near_best', 'missed'} counts over every first try
        self.records = {**db.puzzle_records(conn), **db.puzzle_records(conn, lichess_puzzles.ITEM_TYPE)}
        self.queue: List[Puzzle] = []
        self.queue_index: int = -1
        self.current_puzzle: Optional[Puzzle] = None
        self.current_game: Optional[Game] = None
        # "solving" -> accepting moves; "wrong" -> waiting for retry/reveal;
        # "done" -> solved or revealed, waiting for Next
        self.attempt_state: str = "solving"
        self.solved_ids: set[str] = set()   # solved on the first try this session
        self.seen_ids: set[str] = set()     # shown this session — Random prefers unseen ones

        # Background threads (import/analyze) must never call Tk methods
        # directly — not even self.after(), which itself isn't safe to
        # call from a non-main thread on every platform. Instead, worker
        # threads push callables here, and a timer already running on the
        # main thread drains it.
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._stop_event = threading.Event()
        self._busy = False

        self.import_page = ctk.CTkFrame(master, fg_color="transparent")
        # new puzzles get their kind / solution worked out in the background (puzzle_kind.py)
        self._classify_stop: Optional[threading.Event] = None
        if db.get_setting(conn, "puzzle_cost_version") != analyzer.RULES_VERSION:
            # puzzles must cost real winning chances (analyzer.MIN_CHANCES_LOST)
            analyzer.apply_chances_rule(conn)
            db.set_setting(conn, "puzzle_cost_version", analyzer.RULES_VERSION)
        if db.get_setting(conn, "puzzle_sac_version") != analyzer.RULES_SAC_VERSION:
            # sacrifices you missed: labelled, and added below the blunder size too
            analyzer.add_missed_sacrifices(conn)
            db.set_setting(conn, "puzzle_sac_version", analyzer.RULES_SAC_VERSION)
        if db.get_setting(conn, "puzzle_rules_version") != puzzle_kind.RULES_VERSION:
            # new tactic rules: every puzzle is classified again in the background
            puzzle_kind.reset_for_new_rules(conn)
            db.set_setting(conn, "puzzle_rules_version", puzzle_kind.RULES_VERSION)
            db.set_setting(conn, "puzzle_kind_version", puzzle_kind.VERSION)    # nothing left to relabel
        if db.get_setting(conn, "puzzle_kind_version") != puzzle_kind.VERSION:
            puzzle_kind.relabel_defensive(conn)
            db.set_setting(conn, "puzzle_kind_version", puzzle_kind.VERSION)
        self._build_layout()
        self._refresh_game_summary()
        self._reload_queue()
        self.after(100, self._poll_ui_queue)
        self.after(3000, self._classify_new_puzzles)         # any left from before (e.g. a stopped run)
        self.bind("<Destroy>", lambda e: self._pause_classifying(), add="+")

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
        # a steady width (room for the tags row), whatever the game line's length
        ctk.CTkFrame(board_pad, width=CARD_CONTENT_WIDTH, height=0, fg_color="transparent").pack()

        header = ctk.CTkFrame(board_pad, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        theme.button(header, "Play it out", self._play_it_out, height=28, width=96).pack(side="right")
        self.review_button = theme.button(header, "Review game", self._review_game, height=28, width=104)
        self.review_button.pack(side="right", padx=(0, 6))
        self.game_label = theme.label(header, "", "muted", wraplength=300)
        self.game_label.pack(side="left", fill="x")
        # your history on this puzzle: every first try, right / near-best / missed
        tag_row = ctk.CTkFrame(board_pad, fg_color="transparent", height=22)
        tag_row.pack(fill="x", pady=(0, 6))
        self.record_label = theme.label(tag_row, "", "muted")
        self.record_label.pack(side="right")
        self.tags_frame = ctk.CTkFrame(tag_row, fg_color="transparent")
        self.tags_frame.pack(side="left", fill="x", expand=True)
        self._tag_font = tkfont.Font(font=theme.FONT_SMALL)

        board_row = ctk.CTkFrame(board_pad, fg_color="transparent")
        board_row.pack()
        self.eval_bar = EvalBar(board_row, BOARD_PIXELS)
        self.eval_bar.pack(side="left", padx=(0, 8))
        self.board_widget = ChessBoardWidget(board_row, self._on_move_attempt, bg=theme.PANEL_BG)
        self.board_widget.pack(side="left")

        # buttons right under the board, text below them: a long explanation can
        # then never push the buttons off a short screen
        button_row = ctk.CTkFrame(board_pad, fg_color="transparent")
        button_row.pack(pady=(10, 0), fill="x")
        self.prev_button = theme.button(button_row, "← Prev", self._go_previous, width=88)
        self.prev_button.pack(side="left")
        # step through the main line (packed once the puzzle is solved or revealed)
        self.line_back_button = theme.button(button_row, "◀", lambda: self._step_line(-1), width=36)
        self.line_fwd_button = theme.button(button_row, "▶", lambda: self._step_line(+1), width=36)
        # after a puzzle: analyse the position yourself, Stockfish alongside (shown with ◀ ▶)
        self.study_button = theme.button(button_row, "Analyze", self._toggle_analysis, width=84)
        self.retry_button = theme.button(button_row, "Try again", self._on_retry, width=100)
        self.reveal_button = theme.button(button_row, "Show answer", self._on_reveal, width=120)
        # retry/reveal are packed only after a wrong attempt
        self.next_button = theme.button(button_row, "Next →", self._go_next, "primary", width=104)
        self.next_button.pack(side="right")
        self.random_button = theme.button(button_row, "Random", self._go_random, width=92)
        self.random_button.pack(side="right", padx=(0, 6))

        self.status_label = theme.label(board_pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(pady=(10, 0), anchor="w")
        self.explanation_label = theme.label(board_pad, "", "body", wraplength=BOARD_PIXELS)
        self.explanation_label.pack(pady=(2, 0), anchor="w")
        # self-analysis after a puzzle, from its start: Stockfish's top lines (analysis_board.py)
        self.ideas_frame = IdeasPanel(board_pad, lambda i: self.analysis.follow(i), header_beside=True)
        self.analysis = Analysis(self, self.board_widget, self.ideas_frame, self.eval_bar,
                                 title=self._analysis_title)

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)

        # ---- Games card ----
        theme.label(self.import_page, "Import & Analyze", "title").pack(anchor="w", padx=24, pady=(20, 4))
        theme.label(self.import_page, "Download games from chess.com, then let Stockfish find your "
                                      "mistakes. They become puzzles in Puzzle Review.", "muted").pack(
            anchor="w", padx=24, pady=(0, 12))
        cards = ctk.CTkFrame(self.import_page, fg_color="transparent")
        cards.pack(anchor="nw", padx=24)
        games_card = theme.card(cards)
        games_card.pack(side="left", anchor="n")
        self._build_lichess_card(cards)
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

        # one Stockfish per CPU core, games side by side (~1.7x faster on 2 cores)
        self.parallel_var = tk.BooleanVar(value=db.get_setting(self.conn, "parallel_engines") != "0")
        if analyzer.PARALLEL_ENGINES > 1:
            ctk.CTkCheckBox(
                games, text="Use both CPU cores (faster; the laptop runs warmer)",
                variable=self.parallel_var, font=theme.FONT_SMALL, text_color=theme.TEXT_SOFT,
                fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER_STRONG,
                checkbox_width=18, checkbox_height=18,
                command=lambda: db.set_setting(self.conn, "parallel_engines",
                                               "1" if self.parallel_var.get() else "0"),
            ).pack(anchor="w", pady=(10, 0))

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
        self.classify_label = theme.label(games, "", "muted", wraplength=340)
        self.classify_label.pack(anchor="w", pady=(4, 0))

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

        source_row = ctk.CTkFrame(queue_box, fg_color="transparent")
        source_row.pack(fill="x", pady=(0, 8))
        theme.label(source_row, "Puzzles", "muted").pack(side="left")
        self.source_var = tk.StringVar(value=db.get_setting(self.conn, "puzzle_source") or MY_GAMES)
        if self.source_var.get() not in SOURCE_OPTIONS:
            self.source_var.set(MY_GAMES)
        theme.option_menu(source_row, SOURCE_OPTIONS, self.source_var, lambda _: self._on_source_filter(),
                          width=230).pack(side="right")

        kind_row = ctk.CTkFrame(queue_box, fg_color="transparent")
        kind_row.pack(fill="x", pady=(0, 8))
        theme.label(kind_row, "Kind", "muted").pack(side="left")
        # attacking / defensive / positional, or multi-step only (see puzzle_kind.py)
        self.kind_var = tk.StringVar(value=db.get_setting(self.conn, "puzzle_kind_filter") or ALL_KINDS)
        if self.kind_var.get() not in KIND_OPTIONS:
            self.kind_var.set(ALL_KINDS)
        theme.option_menu(kind_row, KIND_OPTIONS, self.kind_var, lambda _: self._on_kind_filter(),
                          width=230).pack(side="right")

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

    # ---------- Lichess puzzles (lichess_puzzles.py) ----------

    def _build_lichess_card(self, parent) -> None:
        card = theme.card(parent)
        card.pack(side="left", anchor="n", padx=(16, 0))
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.pack(fill="x", padx=18, pady=16)
        theme.label(box, "Lichess puzzles", "heading").pack(anchor="w")
        theme.label(box, "Free puzzles from the Lichess database, kept apart from your own: pick them "
                         "with Puzzle Review's Puzzles dropdown. They earn XP but don't change your "
                         "stats or weak spots.", "muted", wraplength=300).pack(anchor="w", pady=(6, 10))
        self.lichess_summary = theme.label(box, "", "body", wraplength=300)
        self.lichess_summary.pack(anchor="w", pady=(0, 10))

        low, high = lichess_puzzles.default_range(explorer.my_recent_rating(self.conn))
        self.li_min_var = tk.StringVar(value=db.get_setting(self.conn, "lichess_min_rating") or str(low))
        self.li_max_var = tk.StringVar(value=db.get_setting(self.conn, "lichess_max_rating") or str(high))
        self.li_count_var = tk.StringVar(value=db.get_setting(self.conn, "lichess_count")
                                         or str(lichess_puzzles.DEFAULT_COUNT))
        rating_row = ctk.CTkFrame(box, fg_color="transparent")
        rating_row.pack(fill="x", pady=3)
        theme.label(rating_row, "Rating").pack(side="left")
        theme.entry(rating_row, self.li_min_var, width=64).pack(side="left", padx=(10, 6))
        theme.label(rating_row, "to", "muted").pack(side="left")
        theme.entry(rating_row, self.li_max_var, width=64).pack(side="left", padx=6)
        count_row = ctk.CTkFrame(box, fg_color="transparent")
        count_row.pack(fill="x", pady=3)
        theme.label(count_row, "How many").pack(side="left")
        theme.entry(count_row, self.li_count_var, width=74).pack(side="left", padx=10)
        theme.label(count_row, "puzzles", "muted").pack(side="left")

        self.li_add_button = theme.button(box, "Add Lichess puzzles", self._add_lichess_puzzles, "primary")
        self.li_add_button.pack(fill="x", pady=(12, 6))
        self.li_stop_button = theme.button(box, "Stop (keeps what's added)", self._stop_lichess)
        self.li_progress = theme.label(box, "", "muted", wraplength=300)
        self.li_progress.pack(anchor="w")
        self.li_remove_button = theme.button(box, "Remove all Lichess puzzles", self._remove_lichess_puzzles)
        self.li_remove_button.pack(fill="x", pady=(6, 0))
        self._li_stop: Optional[threading.Event] = None
        self._refresh_lichess_summary()

    def _refresh_lichess_summary(self) -> None:
        n = lichess_puzzles.count(self.conn)
        if n:
            low, high = lichess_puzzles.rating_span(self.conn)
            text = f"{n:,} puzzles added, rated {low}–{high}."
        else:
            text = "None added yet."
        self.lichess_summary.configure(text=text)

    def _add_lichess_puzzles(self) -> None:
        if self._li_stop is not None:
            return
        try:
            low, high, count = int(self.li_min_var.get()), int(self.li_max_var.get()), int(self.li_count_var.get())
            if low > high or count <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Lichess puzzles", "Enter a rating range (low to high) and how many puzzles.",
                                 parent=self.winfo_toplevel())
            return
        for key, value in (("lichess_min_rating", low), ("lichess_max_rating", high), ("lichess_count", count)):
            db.set_setting(self.conn, key, str(value))
        skip = lichess_puzzles.ids(self.conn)          # read here: only the main thread touches the DB
        stop = threading.Event()
        self._li_stop = stop
        self.li_add_button.configure(state="disabled")
        self.li_remove_button.configure(state="disabled")
        self.li_stop_button.pack(fill="x", pady=(0, 6), before=self.li_progress)
        self.li_progress.configure(text="Connecting to database.lichess.org…")

        def on_batch(batch):
            self._ui_queue.put(lambda: lichess_puzzles.save(self.conn, batch))

        def on_progress(found, scanned):
            self._ui_queue.put(lambda: self.li_progress.configure(
                text=f"{found:,} of {count:,} found · {scanned:,} Lichess puzzles checked"))

        def worker():
            error = None
            try:
                found = lichess_puzzles.download(low, high, count, skip, on_batch, stop, on_progress)
            except Exception as e:                      # no network, Lichess down, …
                found, error = 0, e
            self._ui_queue.put(lambda: self._lichess_done(found, error))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_lichess(self) -> None:
        if self._li_stop is not None:
            self._li_stop.set()
            self.li_progress.configure(text="Stopping…")

    def _lichess_done(self, found: int, error) -> None:
        stopped = self._li_stop is not None and self._li_stop.is_set()
        self._li_stop = None
        self.li_stop_button.pack_forget()
        self.li_add_button.configure(state="normal")
        self.li_remove_button.configure(state="normal")
        if error is not None:
            self.li_progress.configure(text=f"Couldn't download from Lichess ({type(error).__name__}). "
                                            "Check the internet connection and try again.")
        else:
            self.li_progress.configure(text=f"Added {found:,} puzzles" + (" (stopped)." if stopped else ".")
                                       + (" Choose Lichess in Puzzle Review's Puzzles dropdown."
                                          if self.source_var.get() == MY_GAMES and found else ""))
        self._refresh_lichess_summary()
        if found and self.source_var.get() != MY_GAMES:
            self._reload_queue()

    def _remove_lichess_puzzles(self) -> None:
        n = lichess_puzzles.count(self.conn)
        if not n or not messagebox.askyesno(
                "Lichess puzzles", f"Remove all {n:,} Lichess puzzles? Your XP from them is kept.",
                parent=self.winfo_toplevel()):
            return
        lichess_puzzles.remove_all(self.conn)
        self.li_progress.configure(text="Removed.")
        self._refresh_lichess_summary()
        if self.source_var.get() != MY_GAMES:
            self._reload_queue()

    # ---------- tags (puzzle_tags.py) ----------

    def _refresh_tags(self) -> None:
        """Chips for the puzzle's tags and yours (amber, click to remove), then
        "+ Tag". What doesn't fit goes into "+N", which opens the tag picker."""
        for w in self.tags_frame.winfo_children():
            w.destroy()
        p = self.current_puzzle
        if p is None:
            return
        sharp = style.puzzle_sharpness(self.conn, [p]).get(p.id, False)
        auto = puzzle_tags.auto_tags(p, sharp, stats.is_endgame(p.fen))
        mine = [t for t in puzzle_tags.user_tags(self.conn, p) if t.lower() not in {a.lower() for a in auto}]
        self._shown_tags = (auto, mine)
        room = (self.tags_frame.master.winfo_width() if self.tags_frame.master.winfo_width() > 50
                else BOARD_PIXELS + 30)
        room -= self._tag_font.measure(self.record_label.cget("text") or "You: ✓0 ◐0 ✗0") + 12
        room -= self._chip_width("+ Tag") + 4
        # the kind first, then yours (so they're never the ones hidden), then the rest
        chips = [(t, False) for t in auto[:1]] + [(t, True) for t in mine] + [(t, False) for t in auto[1:]]
        used, hidden = 0, 0
        for i, (tag, yours) in enumerate(chips):
            text = f"{tag}  ×" if yours else tag
            need = self._chip_width(text) + 4
            more = len(chips) - i - 1
            if used + need + (self._chip_width(f"+{more}") + 4 if more else 0) > room:
                hidden = len(chips) - i
                break
            self._make_chip(text, yours, (lambda t=tag: self._remove_tag(t)) if yours else None)
            used += need
        if hidden:
            self._make_chip(f"+{hidden}", False, self._open_tag_menu)
        self._make_chip("+ Tag", True, self._open_tag_menu, add=True)

    def _chip_width(self, text: str) -> int:
        return self._tag_font.measure(text) + 16

    def _make_chip(self, text: str, yours: bool, command=None, add: bool = False):
        chip = ctk.CTkLabel(self.tags_frame, text=text, height=20, width=self._chip_width(text),
                            corner_radius=8, font=theme.FONT_SMALL,
                            fg_color="transparent" if add else theme.CONTROL_BG if yours else theme.PANEL_ALT,
                            text_color=theme.ACCENT if yours else theme.TEXT_SOFT,
                            cursor="hand2" if command else "")
        chip.pack(side="left", padx=(0, 4))
        if command:
            chip.bind("<Button-1>", lambda e: command())
        self._last_chip = chip
        return chip

    def _open_tag_menu(self) -> None:
        """A small window: this puzzle's tags, then tag buttons to click on /
        off (amber = yours) and a box to type your own. Stays open until Done."""
        if self.current_puzzle is None:
            return
        self._close_tag_picker()
        auto, _ = self._shown_tags
        win = ctk.CTkToplevel(self)
        win.title("Tags")
        win.configure(fg_color=theme.BG)
        win.resizable(False, False)
        win.transient(self.winfo_toplevel())
        win.protocol("WM_DELETE_WINDOW", self._close_tag_picker)
        win.bind("<Escape>", lambda e: self._close_tag_picker())
        self._tag_picker = win
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=16)
        theme.label(body, "THIS PUZZLE", "section").pack(anchor="w")
        theme.label(body, ", ".join(auto) or "No tags yet", "body", wraplength=440).pack(anchor="w", pady=(2, 12))
        theme.label(body, "YOUR TAGS — click to add or remove", "section").pack(anchor="w", pady=(0, 6))
        self._picker_grid = ctk.CTkFrame(body, fg_color="transparent")
        self._picker_grid.pack(anchor="w")
        self._fill_tag_picker()

        add_row = ctk.CTkFrame(body, fg_color="transparent")
        add_row.pack(fill="x", pady=(12, 0))
        self._new_tag_var = tk.StringVar()
        theme.label(add_row, "Your own", "muted").pack(side="left", padx=(0, 8))
        entry = theme.entry(add_row, self._new_tag_var, width=200)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self._add_typed_tag())
        theme.button(add_row, "Add", self._add_typed_tag, height=32, width=64).pack(side="left", padx=(8, 0))
        theme.button(add_row, "Done", self._close_tag_picker, "primary", height=32, width=72).pack(side="right")

        win.update_idletasks()
        x = min(self._last_chip.winfo_rootx(), win.winfo_screenwidth() - win.winfo_reqwidth() - 10)
        y = min(self._last_chip.winfo_rooty() + 26, win.winfo_screenheight() - win.winfo_reqheight() - 40)
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.after(50, lambda: (win.lift(), entry.focus_set()))

    def _fill_tag_picker(self) -> None:
        for w in self._picker_grid.winfo_children():
            w.destroy()
        auto, mine = self._shown_tags
        have = {t.lower() for t in auto}
        offered = [t for t in puzzle_tags.SUGGESTED if t.lower() not in have]
        offered += [t for t in mine if t not in puzzle_tags.SUGGESTED]
        for i, tag in enumerate(offered):
            on = tag in mine
            ctk.CTkButton(
                self._picker_grid, text=tag, height=28, width=104, corner_radius=8, font=theme.FONT_SMALL,
                fg_color=theme.ACCENT if on else theme.PANEL_ALT, hover_color=theme.ACCENT_HOVER if on else theme.CONTROL_BG,
                text_color=theme.ACCENT_TEXT if on else theme.TEXT_SOFT, border_width=0,
                command=lambda t=tag, on=on: self._toggle_tag(t, not on),
            ).grid(row=i // 4, column=i % 4, padx=(0, 6), pady=(0, 6), sticky="w")

    def _close_tag_picker(self) -> None:
        win = getattr(self, "_tag_picker", None)
        self._tag_picker = None
        if win is not None and win.winfo_exists():
            win.destroy()

    def _add_typed_tag(self) -> None:
        tag = self._new_tag_var.get().strip()
        if tag and self.current_puzzle is not None:
            puzzle_tags.add(self.conn, self.current_puzzle, tag)
            self._new_tag_var.set("")
            self._tags_changed()

    def _toggle_tag(self, tag: str, on: bool) -> None:
        (puzzle_tags.add if on else puzzle_tags.remove)(self.conn, self.current_puzzle, tag)
        self._tags_changed()

    def _remove_tag(self, tag: str) -> None:
        puzzle_tags.remove(self.conn, self.current_puzzle, tag)
        self._tags_changed()

    def _tags_changed(self) -> None:
        self._refresh_tags()
        if getattr(self, "_tag_picker", None) is not None and self._tag_picker.winfo_exists():
            self._fill_tag_picker()
        # the Type filter's tag list (without rebuilding the queue you're in)
        self._user_tags = puzzle_tags.all_user_tags(self.conn)
        values = [v for v in self.type_filter_dropdown.cget("values") if not v.startswith(TAG_PREFIX)]
        self.type_filter_dropdown.configure(
            values=values + [TAG_PREFIX + t for t in sorted(self._user_tags, key=str.lower)])

    # ---------- opened from elsewhere (Progress) ----------

    def _clear_filters(self, source: str) -> None:
        self.source_var.set(source)
        self.kind_var.set(ALL_KINDS)
        self.position_var.set(ALL_POSITIONS)
        self.type_filter_var.set("All types")

    def practice_theme(self, tag: str) -> None:
        """The queue: every puzzle (yours and Lichess) with this tag, unsolved first."""
        self._clear_filters(BOTH_SOURCES)
        self.type_filter_var.set(THEME_PREFIX + tag)
        self._reload_queue()

    def open_puzzle(self, puzzle_id: str) -> bool:
        """Shows one of your games' puzzles (filters cleared so it's in the queue)."""
        self._clear_filters(MY_GAMES)
        self.focus_var.set("All games")
        self._reload_queue()
        index = next((i for i, p in enumerate(self.queue) if p.id == puzzle_id), None)
        if index is None:
            return False
        self._load_puzzle_at(index)
        return True

    def _on_source_filter(self) -> None:
        db.set_setting(self.conn, "puzzle_source", self.source_var.get())
        self._reload_queue()

    def _on_kind_filter(self) -> None:
        db.set_setting(self.conn, "puzzle_kind_filter", self.kind_var.get())
        self._reload_queue()

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
        source = self.source_var.get()
        mine, lichess = source != LICHESS, source != MY_GAMES
        types = set(db.distinct_puzzle_types(self.conn)) if mine else set()
        if lichess:
            types |= {lichess_puzzles.puzzle_type((r[0] or "").split())
                      for r in self.conn.execute("SELECT DISTINCT themes FROM lichess_puzzles")}
        self._user_tags = puzzle_tags.all_user_tags(self.conn)
        options = (["All types", WEAK_SPOTS, ENDGAMES] + sorted(types)
                   + [TAG_PREFIX + t for t in sorted(self._user_tags, key=str.lower)])
        if self.type_filter_var.get().startswith(THEME_PREFIX):
            options.append(self.type_filter_var.get())
        self.type_filter_dropdown.configure(values=options)
        if self.type_filter_var.get() not in options:
            self.type_filter_var.set("All types")

        selected_type = self.type_filter_var.get()
        filter_type = (None if selected_type in ("All types", WEAK_SPOTS, ENDGAMES)
                       or selected_type.startswith((TAG_PREFIX, THEME_PREFIX)) else selected_type)
        self.queue = (db.all_puzzles(self.conn, puzzle_type=filter_type, time_classes=self._focus_classes())
                      if mine else [])
        if lichess:            # the Games (time control) filter is about your games only
            self.queue += lichess_puzzles.load(self.conn, filter_type)
            if mine:
                random.shuffle(self.queue)
        if selected_type == WEAK_SPOTS:
            # puzzles you keep missing, weakest first (solve twice in a row to clear one)
            weak = {**db.weak_item_ids(self.conn, "puzzle"), **db.weak_item_ids(self.conn, lichess_puzzles.ITEM_TYPE)}
            self.queue = sorted((p for p in self.queue if p.id in weak), key=lambda p: -weak[p.id])
        elif selected_type == ENDGAMES:
            self.queue = [p for p in self.queue if stats.is_endgame(p.fen)]
        elif selected_type.startswith(TAG_PREFIX):
            keys = self._user_tags.get(selected_type[len(TAG_PREFIX):], set())
            self.queue = [p for p in self.queue if puzzle_tags.key(p) in keys]
        elif selected_type.startswith(THEME_PREFIX):
            want = selected_type[len(THEME_PREFIX):].lower()
            keys = {k for t, ks in self._user_tags.items() if t.lower() == want for k in ks}
            self.queue = [p for p in self.queue if puzzle_tags.key(p) in keys or want in
                          {t.lower() for t in puzzle_tags.auto_tags(p, False, stats.is_endgame(p.fen))}]
            # ones you haven't solved yet first
            self.queue.sort(key=lambda p: (self.records.get(p.id) or {}).get("correct", 0) > 0)
        kind = self.kind_var.get()
        if kind == MULTI_STEP:
            self.queue = [p for p in self.queue if (p.solution_moves or 0) > 1]
        elif kind != ALL_KINDS:
            self.queue = [p for p in self.queue if p.kind == kind]
        position = self.position_var.get()
        if position != ALL_POSITIONS:
            # sharp = one clearly best move (engine's top choice far ahead of its 2nd)
            sharp = style.puzzle_sharpness(self.conn, self.queue)
            self.queue = [p for p in self.queue if sharp.get(p.id) == (position == SHARP_ONLY)]
        empty_text = {
            WEAK_SPOTS: "No weak spots right now. Puzzles you miss show up here until you "
                        "solve them twice in a row.",
            ENDGAMES: "No endgame puzzles in this filter yet.",
        }.get(selected_type, "No puzzles match these filters." if position != ALL_POSITIONS or kind != ALL_KINDS else
               "No Lichess puzzles yet. Add some on Import & Analyze → Lichess puzzles." if source == LICHESS else
               "No puzzles yet. Import your games, then click Analyze new games.")

        self._refresh_listbox()
        if self.queue:
            self._load_puzzle_at(0)
        else:
            self.queue_index = -1
            self.current_puzzle = None
            self.current_game = None
            self._refresh_tags()
            self.board_widget.clear()
            self.game_label.configure(text="")
            self.record_label.configure(text="")
            self.status_label.configure(text=empty_text, text_color=theme.TEXT_MUTED)
            self.explanation_label.configure(text="")
            self._hide_retry_buttons()

    def _listbox_label(self, index: int) -> str:
        p = self.queue[index]
        check = "✓ " if p.id in self.solved_ids else "   "
        record = _record_short(self.records.get(p.id))
        if p.source == PuzzleSource.LICHESS:        # its rating; "Li" marks it in a mixed queue
            detail = f"Li {p.rating}" if self.source_var.get() == BOTH_SOURCES else str(p.rating)
        else:
            detail = _swing_text(p)
        return (f"{check}{index + 1}.  {p.puzzle_type or 'Puzzle'}  ({detail})"
                + (f"   {record}" if record else ""))

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

    def _go_random(self) -> None:
        """Jumps to a random puzzle in the queue, preferring ones not yet
        shown this session; once every puzzle has been seen, any puzzle
        other than the current one."""
        if not self.queue:
            return
        others = [i for i in range(len(self.queue)) if i != self.queue_index] or [self.queue_index]
        unseen = [i for i in others if self.queue[i].id not in self.seen_ids]
        self._load_puzzle_at(random.choice(unseen or others))

    def _load_puzzle_at(self, index: int) -> None:
        self.attempt_state = "solving"
        self._hide_retry_buttons()
        self.explanation_label.configure(text="")
        self.queue_index = index
        self.current_puzzle = self.queue[index]
        self.seen_ids.add(self.current_puzzle.id)
        self._attempt_recorded = False
        self._attempt_id = None
        self._hide_main_line()
        self._start_solution()
        self._near_best_flagged = False
        self._explain_token = None
        self.current_game = (
            db.get_game(self.conn, self.current_puzzle.source_game_id)
            if self.current_puzzle.source_game_id else None
        )
        board = chess.Board(self.current_puzzle.fen)
        # puzzle positions are always your move — show the board from your side
        self._show_start_position(flipped=board.turn == chess.BLACK)
        self.review_button.configure(text="Open on Lichess" if self._is_lichess() else "Review game",
                                     width=120 if self._is_lichess() else 104)
        self.game_label.configure(text=self._game_label_text())
        self._update_record_label()
        self._close_tag_picker()
        self._refresh_tags()
        self._show_prompt()
        self._set_eval(self.current_puzzle.eval_before_cp)

        self.queue_listbox.selection_clear(0, tk.END)
        self.queue_listbox.selection_set(index)
        self.queue_listbox.see(index)

    def _is_lichess(self) -> bool:
        return self.current_puzzle is not None and self.current_puzzle.source == PuzzleSource.LICHESS

    def _item_type(self) -> str:
        """attempts.item_type: Lichess tries are kept apart from your games' puzzles."""
        return lichess_puzzles.ITEM_TYPE if self._is_lichess() else "puzzle"

    def _show_start_position(self, flipped: Optional[bool] = None) -> None:
        """The puzzle's position; a Lichess puzzle shows the opponent's move
        that set it up as the last move."""
        p = self.current_puzzle
        if p.setup_fen and p.setup_uci:
            self.board_widget.load_fen(p.setup_fen, flipped=flipped)
            self.board_widget.board.push(chess.Move.from_uci(p.setup_uci))
            self.board_widget.redraw()
        else:
            self.board_widget.load_fen(p.fen, flipped=flipped)

    def _show_prompt(self) -> None:
        p = self.current_puzzle
        side = "White" if chess.Board(p.fen).turn == chess.WHITE else "Black"
        setup = ""
        if p.setup_fen and p.setup_uci:
            setup = f"Opponent played {chess.Board(p.setup_fen).san(chess.Move.from_uci(p.setup_uci))}. "
        self.status_label.configure(text=f"{setup}Find the best move for {side}.", text_color=theme.TEXT)

    def _game_label_text(self) -> str:
        if self._is_lichess():
            p = self.current_puzzle
            return f"Lichess puzzle, rated {p.rating}" + (f" — {p.opening}" if p.opening else "")
        if self.current_game is None:
            return ""
        my_username = db.get_setting(self.conn, "chess_com_username") or "You"
        if self.current_game.my_color.value == "white":
            white, black = my_username, self.current_game.opponent_username
        else:
            white, black = self.current_game.opponent_username, my_username
        played = self.current_game.played_at.strftime("%b %d, %Y")
        return f"{white} (White) vs {black} (Black) — {played}"

    # ---------- Attempting a puzzle ----------

    def _on_move_attempt(self, move: chess.Move) -> None:
        if self.analysis.active:
            self.analysis.play(move)
            return
        if self.current_puzzle is None or self.attempt_state != "solving":
            return
        if self._ply > 0:
            self._on_sequence_move(move)
            return

        board = self.board_widget.board
        played_san = board.san(move)
        correct = move.uci() == self.current_puzzle.correct_move_uci
        rank, cp_alt = (None, None) if correct else self._engine_rank(move)

        board.push(move)
        self.board_widget.redraw()
        first_try = not self._attempt_recorded
        # XP is paid once per puzzle per day — only promise a bonus that's real
        pays_xp = first_try and not db.tried_today(self.conn, self._item_type(), self.current_puzzle.id)
        if first_try:
            # only the first try counts toward Weak spots / Progress / XP
            outcome = "correct" if correct else "near_best" if rank else "missed"
            self._save_first_try(correct, outcome)
            self._run_attempt_id = self._attempt_id
            self._pays_xp = pays_xp
        if not correct:
            self._seq_missed = True

        if correct:
            self._seq_board.push(move)
            if not self._solution_ready:
                # the forcing line is still being worked out — continue when it's ready
                self.attempt_state = "pending"
                self.status_label.configure(text=f"✓ {played_san} — checking whether the line continues…",
                                            text_color=theme.SUCCESS)
                return
            self._after_correct(played_san)
        else:
            self.attempt_state = "wrong"
            if rank:
                self._flag_near_best(played_san, f"the engine's {rank} choice")
                if cp_alt is not None:
                    self._set_eval(cp_alt)
            else:
                self.status_label.configure(
                    text=f"Not quite — {played_san} isn't the best move here.",
                    text_color=theme.ERROR,
                )
            self._show_retry_buttons()
            fen = self.current_puzzle.fen
            self.explanation_label.configure(text="Working out why…")
            self.board_widget.set_marked_arrows(self._game_move_arrows())
            self._explain_async(lambda engine: self._attempt_job(fen, move, engine))

    # ---------- move explanations (Stockfish lines + board facts) ----------

    def _explain_solution_async(self, solved: bool) -> None:
        p = self.current_puzzle
        best = chess.Move.from_uci(p.correct_move_uci)
        try:
            game_move = chess.Board(p.fen).parse_san(p.played_move_san) if p.played_move_san else None
        except ValueError:
            game_move = None
        summary = self._eval_summary()
        # the line so far: everything you played if solved (the board shows its end),
        # else the full solution with the board on its first move
        if solved and self._seq_board is not None and self._seq_board.move_stack:
            line, shown = list(self._seq_board.move_stack), len(self._seq_board.move_stack)
        else:
            line, shown = list(self._solution) or [best], 1
        self._load_main_line(p.fen, line, shown)
        self.explanation_label.configure(text="Working out the key line…")
        self._explain_async(lambda engine: explain.explain_solution(p.fen, best, game_move, engine)
                            + (f"\n{summary}" if summary else ""))

    # ---------- step-through main line ----------

    def _load_main_line(self, fen: str, start: List[chess.Move], shown: int) -> None:
        """Shows ◀ ▶ right away (the solution so far; the board shows `shown`
        moves of it), then fetches Stockfish's continuation after it in the
        background and extends the line."""
        token = object()
        self._line_token = token
        self._main_line, self._line_index = list(start), shown
        self.line_back_button.pack(side="left", padx=(8, 0))
        self.line_fwd_button.pack(side="left", padx=(6, 0))
        self.study_button.configure(text="Analyze")
        self.study_button.pack(side="left", padx=(6, 0))
        self._update_line_buttons()

        def worker():
            line: List[chess.Move] = []
            try:
                board = chess.Board(fen)
                for move in start:
                    board.push(move)
                if not board.is_game_over():
                    with engine_service.borrow() as engine:
                        info = engine.analyse(board, chess.engine.Limit(depth=MAIN_LINE_DEPTH))
                    line = list(info.get("pv", []))[:MAIN_LINE_PLIES]
            except Exception:
                line = []

            def apply():
                if self._line_token is token and line:
                    self._main_line = list(start) + line
                    self._update_line_buttons()
            self._ui_queue.put(apply)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- self-analysis after a puzzle (analysis_board.py) ----------

    def _toggle_analysis(self) -> None:
        if self.analysis.active:
            self._stop_analysis()
            self._step_line(0)                  # back to the solution view
            return
        if self.current_puzzle is None or self.attempt_state != "done":
            return
        self.study_button.configure(text="Stop analysis", width=120)
        for b in (self.line_back_button, self.line_fwd_button):
            b.configure(state="normal")
        self.explanation_label.pack_forget()
        self.ideas_frame.pack(after=self.status_label, anchor="w", fill="x", pady=(10, 0))
        self.status_label.pack_forget()
        # from the puzzle's start: ▶ walks the solution, any move branches off
        self.analysis.start(chess.Board(self.current_puzzle.fen), ahead=self._main_line)

    def _stop_analysis(self) -> None:
        if not self.analysis.active:
            return
        self.analysis.stop()
        self.study_button.configure(text="Analyze", width=84)
        self.status_label.pack(before=self.ideas_frame, pady=(10, 0), anchor="w")
        self.ideas_frame.pack_forget()
        if not self.explanation_label.winfo_manager():
            self.explanation_label.pack(after=self.status_label, pady=(2, 0), anchor="w")

    @staticmethod
    def _analysis_title(board: chess.Board) -> str:
        made = len(board.move_stack)
        side = "White" if board.turn == chess.WHITE else "Black"
        return f"{side} to move" + (f" ({made} move{'s' if made != 1 else ''} in)" if made else "")

    def _hide_main_line(self) -> None:
        self._line_token = None
        self._main_line, self._line_index = [], 0
        self.line_back_button.pack_forget()
        self.line_fwd_button.pack_forget()
        self.study_button.pack_forget()
        self._stop_analysis()

    def _update_line_buttons(self) -> None:
        if self.analysis.active:
            return                               # ◀ ▶ step the analysis board then
        self.line_back_button.configure(state="normal" if self._line_index > 0 else "disabled")
        self.line_fwd_button.configure(
            state="normal" if self._line_index < len(self._main_line) else "disabled")

    def _step_line(self, delta: int) -> None:
        if self.analysis.active:
            self.analysis.step(delta)
            return
        if self.current_puzzle is None or self.attempt_state != "done" or not self._main_line:
            return
        self._line_index = max(0, min(len(self._main_line), self._line_index + delta))
        self._show_start_position()
        board = self.board_widget.board
        sans = []
        for move in self._main_line[:self._line_index]:
            sans.append(board.san(move))
            board.push(move)
        self.board_widget.redraw()
        self._update_line_buttons()
        start = chess.Board(self.current_puzzle.fen)
        text = _line_text(sans, start) if sans else "the puzzle position"
        more = " …" if self._line_index < len(self._main_line) else ""
        self.status_label.configure(
            text=f"Main line ({self._line_index}/{len(self._main_line)}): {text}{more}",
            text_color=theme.TEXT)

    def _explain_async(self, job) -> None:
        """Runs job(engine) on a background thread with a short-lived
        Stockfish (None if not installed) and shows the text when done —
        unless the user has moved on to another puzzle or retried."""
        token = object()
        self._explain_token = token

        def worker():
            try:
                if engine_service.available():
                    with engine_service.borrow() as engine:
                        text = job(engine)
                else:
                    text = job(None)
            except Exception:
                text = None

            def apply():
                if self._explain_token is not token or not text:
                    return
                if isinstance(text, tuple):          # (explanation, arrows, eval after your try)
                    words, arrows, cp_after = text
                    self.explanation_label.configure(text=words)
                    if self.attempt_state == "wrong":
                        self.board_widget.set_marked_arrows(arrows)
                        if cp_after is not None:
                            self._set_eval(cp_after)
                            self._maybe_flag_close(cp_after)
                        if self._near_best_flagged:     # a good move: drop the "you miss" wording
                            self.explanation_label.configure(
                                text=words.replace(" — you miss something stronger here", ""))
                else:
                    self.explanation_label.configure(text=text)
            self._ui_queue.put(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _attempt_job(self, fen: str, move: chess.Move, engine):
        """Explanation of a wrong try + the computer's reply as a green arrow."""
        text, line, cp_after = explain.explain_attempt(fen, move, engine, with_line=True)
        arrows = self._game_move_arrows()
        if line:
            arrows.append((line[0].from_square, line[0].to_square, REPLY_COLOR))
            board = chess.Board(fen)
            board.push(move)
            text += f"\nGreen arrow: the computer's reply {board.san(line[0])}."
        if any(color == GAME_MOVE_COLOR for _, _, color in arrows):
            text += f" Blue arrow: your game move {self.current_puzzle.played_move_san}."
        return text, arrows, cp_after

    # ---------- evaluation bar & near-best moves ----------

    def _set_eval(self, cp_me: Optional[int]) -> None:
        """Evaluation from YOUR point of view (the side to move in the puzzle)."""
        if cp_me is None or self.current_puzzle is None:
            self.eval_bar.set(None, flipped=self.board_widget.flipped)
            return
        white = chess.Board(self.current_puzzle.fen).turn == chess.WHITE
        self.eval_bar.set(cp_me if white else -cp_me, flipped=self.board_widget.flipped)

    def _engine_rank(self, move: chess.Move):
        """('2nd' / '3rd', eval) if the try was one of the engine's saved top
        choices for this exact position, else (None, None)."""
        p = self.current_puzzle
        if not p.source_game_id:
            return None, None
        row = self.conn.execute(
            """SELECT second_move_uci, cp_second, third_move_uci, cp_third FROM move_evaluations
               WHERE game_id = ? AND fen_before = ?""", (p.source_game_id, p.fen)).fetchone()
        if row is None:
            return None, None
        if row["second_move_uci"] == move.uci():
            return "2nd", row["cp_second"]
        if row["third_move_uci"] == move.uci():
            return "3rd", row["cp_third"]
        return None, None

    def _flag_near_best(self, san: str, what: str) -> None:
        self._near_best_flagged = True
        self.status_label.configure(text=f"Good find! {san} is {what}. There's one even better.",
                                    text_color=theme.WARNING)

    def _maybe_flag_close(self, cp_after: int) -> None:
        """Live check once the engine has looked at your try: within
        NEAR_BEST_CP of the best move counts as a near-best find (covers
        older puzzles without saved top-3 data)."""
        p = self.current_puzzle
        if self._near_best_flagged or p.eval_before_cp is None:
            return
        gap = p.eval_before_cp - cp_after
        if gap <= NEAR_BEST_CP:
            board = chess.Board(p.fen)
            attempt = self.board_widget.board.move_stack[-1] if self.board_widget.board.move_stack else None
            san = board.san(attempt) if attempt is not None and attempt in board.legal_moves else "That"
            self._flag_near_best(san, f"almost as good as the best move (only {max(gap, 0) / 100:.1f} worse)")
            if self._attempt_id is not None:   # it was your first try: save it as near-best
                db.set_attempt_outcome(self.conn, self._attempt_id, "near_best")
                self._attempt_id = None
                self._count_outcome(p.id, "missed", -1)
                self._count_outcome(p.id, "near_best", +1)
                progression.notify()

    def _game_move_arrows(self):
        """Blue arrow for the move you played in the game - only once you've
        made a move, and only if that piece is still where it started (so the
        arrow never begins on an empty square)."""
        p = self.current_puzzle
        if p is None or not p.played_move_san:
            return []
        try:
            start_board = chess.Board(p.fen)
            move = start_board.parse_san(p.played_move_san)
        except ValueError:
            return []
        now = self.board_widget.board
        if now is None or now.piece_at(move.from_square) != start_board.piece_at(move.from_square):
            return []
        return [(move.from_square, move.to_square, GAME_MOVE_COLOR)]

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
        if self._is_lichess():
            if self.current_puzzle.game_url:
                webbrowser.open(self.current_puzzle.game_url)
            return
        if self.current_puzzle is None or not self.current_puzzle.source_game_id:
            return
        if self.on_review_game:
            self.on_review_game(self.current_puzzle.source_game_id, self.current_puzzle.fen)

    def _save_first_try(self, correct: bool, outcome: str) -> None:
        pid = self.current_puzzle.id
        self._attempt_id = db.record_attempt(self.conn, self._item_type(), pid, correct, outcome,
                                             sharp=self._is_sharp())
        self._attempt_recorded = True
        self._count_outcome(pid, outcome, +1)
        progression.notify()

    def _is_sharp(self) -> bool:
        return style.puzzle_sharpness(self.conn, [self.current_puzzle]).get(self.current_puzzle.id, False)

    def _count_outcome(self, puzzle_id: str, outcome: str, delta: int) -> None:
        rec = self.records.setdefault(puzzle_id, {"correct": 0, "near_best": 0, "missed": 0})
        rec["missed" if outcome == "revealed" else outcome] += delta
        self._refresh_listbox_row(self.queue_index)
        self._update_record_label()

    def _update_record_label(self) -> None:
        rec = self.records.get(self.current_puzzle.id) if self.current_puzzle else None
        if not self.current_puzzle:
            text = ""
        elif not rec or not sum(rec.values()):
            text = "First try"
        else:
            text = f"You: ✓{rec['correct']} ◐{rec['near_best']} ✗{rec['missed']}"
        self.record_label.configure(text=text)

    # ---------- multi-move puzzles ----------

    def _start_solution(self) -> None:
        """Works out the forcing line for this puzzle in the background. Until
        it's known the puzzle is one move; a puzzle that isn't a forcing
        tactic (e.g. the best move just stops a threat) stays one move."""
        p = self.current_puzzle
        best = chess.Move.from_uci(p.correct_move_uci)
        token = object()
        self._solution_token = token
        self._solution, self._solution_ready = [best], False
        self._ply, self._seq_board, self._seq_missed = 0, chess.Board(p.fen), False
        self._run_attempt_id = None

        if p.solution_ucis:                         # worked out before (puzzle_kind.py): instant
            self._solution = [chess.Move.from_uci(u) for u in p.solution_ucis.split()]
            self._solution_ready = True
            return

        def worker():
            solution = [best]
            result = None
            try:
                if engine_service.available():
                    with engine_service.borrow() as engine:
                        result = puzzle_kind.classify(engine, p.fen, p.correct_move_uci)
                    solution = result.solution
            except Exception:
                solution = [best]

            def apply():
                if result is not None:               # save it: next time it's instant, and filterable
                    try:
                        puzzle_kind.save(self.conn, p.id, result)
                        p.solution_ucis = " ".join(m.uci() for m in result.solution)
                        p.solution_moves, p.kind = result.moves, result.kind
                    except Exception:
                        pass
                if self._solution_token is not token:
                    return
                self._solution, self._solution_ready = solution, True
                self._refresh_tags()
                if self.attempt_state == "pending":         # first move already played
                    self._after_correct(chess.Board(p.fen).san(best))
            self._ui_queue.put(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _solution_text(self) -> str:
        board = chess.Board(self.current_puzzle.fen)
        sans = []
        for move in self._solution:
            sans.append(board.san(move))
            board.push(move)
        return _line_text(sans, chess.Board(self.current_puzzle.fen))

    def _after_correct(self, san: str) -> None:
        """A right move: finish the puzzle, or play the opponent's reply."""
        if self._seq_board.is_checkmate() or self._ply + 1 >= len(self._solution):
            self._finish_solved(san)
            return
        self.attempt_state = "reply"
        self.status_label.configure(text=f"✓ {san} — keep going.", text_color=theme.SUCCESS)
        token = self._solution_token
        self.after(REPLY_DELAY_MS, lambda: self._play_reply(token))

    def _play_reply(self, token) -> None:
        if token is not self._solution_token or self.attempt_state != "reply":
            return
        reply = self._solution[self._ply + 1]
        san = self._seq_board.san(reply)
        self._seq_board.push(reply)
        self.board_widget.load_fen(self._seq_board.fen())
        self.board_widget.board = self._seq_board.copy()   # keeps the last-move highlight
        self.board_widget.redraw()
        self._ply += 2
        self.attempt_state = "solving"
        self.status_label.configure(text=f"Opponent played {san}. Your move.", text_color=theme.TEXT)

    def _on_sequence_move(self, move: chess.Move) -> None:
        """Your 2nd, 3rd or 4th move: it has to be the line's move (or mate)."""
        board = self.board_widget.board
        san = board.san(move)
        after = self._seq_board.copy()
        after.push(move)
        board.push(move)
        self.board_widget.redraw()
        if move == self._solution[self._ply] or after.is_checkmate():
            self._seq_board.push(move)
            self._after_correct(san)
            return
        self._mark_sequence_missed()
        self.attempt_state = "wrong"
        self.status_label.configure(text=f"Not quite — {san} isn't the move; the tactic continues differently.",
                                    text_color=theme.ERROR)
        self.explanation_label.configure(text="")
        self._show_retry_buttons()

    def _mark_sequence_missed(self) -> None:
        """A miss after a right first move: the saved first try becomes a miss."""
        if self._seq_missed:
            return
        self._seq_missed = True
        if self._run_attempt_id is not None:
            db.set_attempt_result(self.conn, self._run_attempt_id, False, "missed")
            self._count_outcome(self.current_puzzle.id, "correct", -1)
            self._count_outcome(self.current_puzzle.id, "missed", +1)
            progression.notify()

    def _finish_solved(self, san: str) -> None:
        self.attempt_state = "done"
        clean = not self._seq_missed
        if clean:
            self.solved_ids.add(self.current_puzzle.id)
            self._refresh_listbox_row(self.queue_index)
        bonus = (f"  Sharp position: +{progression.SHARP_BONUS_XP} XP bonus."
                 if clean and self._pays_xp and self._is_sharp() else "")
        if len(self._solution) > 1:
            text = f"Correct — the whole line: {self._solution_text()}!{bonus}"
        else:
            text = f"Correct — {san}!{bonus}"
        if not clean:
            text += "  (Found with retries — only a clean first run counts.)"
        self.status_label.configure(text=text, text_color=theme.SUCCESS)
        # stay on the position you reached; ◀ steps back through your line
        self._show_start_position()
        for move in self._seq_board.move_stack:
            self.board_widget.board.push(move)
        self.board_widget.redraw()
        self.board_widget.set_marked_arrows(self._game_move_arrows())
        self._set_eval(self.current_puzzle.eval_before_cp)
        self.explanation_label.configure(text=self._explanation_text(correct=True))
        self._explain_solution_async(solved=True)

    def _on_retry(self) -> None:
        if self.current_puzzle is None:
            return
        self.attempt_state = "solving"
        self._explain_token = None
        self._attempt_id = None        # retries never change the saved first try
        self._near_best_flagged = False
        self.explanation_label.configure(text="")
        self._hide_retry_buttons()
        self.board_widget.load_fen(self._seq_board.fen())     # where this step of the line starts
        if self._ply:
            self.status_label.configure(text="Try again — your move.", text_color=theme.TEXT)
        else:
            self._show_prompt()
            self._set_eval(self.current_puzzle.eval_before_cp)

    def _on_reveal(self) -> None:
        if self.current_puzzle is None:
            return
        self.attempt_state = "done"
        if not self._attempt_recorded:   # revealed without trying = a miss
            self._save_first_try(False, "revealed")
        self._mark_sequence_missed()
        self._attempt_id = None
        self._hide_retry_buttons()
        self._show_start_position()
        self.board_widget.board.push(chess.Move.from_uci(self.current_puzzle.correct_move_uci))
        self.board_widget.set_marked_arrows(self._game_move_arrows())
        self._set_eval(self.current_puzzle.eval_before_cp)
        if len(self._solution) > 1:
            self.status_label.configure(text=f"The solution: {self._solution_text()}.", text_color=theme.TEXT)
        else:
            self.status_label.configure(
                text=f"The best move was {self.current_puzzle.correct_move_san}.", text_color=theme.TEXT,
            )
        self.explanation_label.configure(text=self._explanation_text(correct=False))
        self._explain_solution_async(solved=False)

    def _show_retry_buttons(self) -> None:
        self.retry_button.pack(side="left", padx=(8, 0))
        self.reveal_button.pack(side="left", padx=(8, 0))

    def _hide_retry_buttons(self) -> None:
        self.retry_button.pack_forget()
        self.reveal_button.pack_forget()

    def _explanation_text(self, correct: bool) -> str:
        p = self.current_puzzle
        if self._is_lichess():
            themes = lichess_puzzles.nice_themes(p.themes)
            return (f"A Lichess puzzle rated {p.rating}" + (f" — themes: {themes}" if themes else "") + "."
                    + (f" Opening: {p.opening}." if p.opening else ""))
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
                    else:                        # reviewed on chess.com since: pick up its accuracy
                        db.update_accuracy(self.conn, g.chess_com_url, g.my_accuracy, g.opp_accuracy)
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

        self._pause_classifying()                # the analysis gets the processor
        self._stop_event.clear()
        self._set_busy(True)
        self._show_progress("Preparing...", maximum=1, stoppable=True)
        # read Tk state here: the worker thread must never touch Tk
        engines = analyzer.PARALLEL_ENGINES if self.parallel_var.get() else 1

        def worker():
            try:
                self._run_analysis(games, stockfish_path, threshold_cp, engines)
            except Exception as e:
                msg = str(e)
                self._ui_queue.put(lambda: self._on_analysis_error(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _run_analysis(self, games: List[Game], stockfish_path: str, threshold_cp: int,
                      engines: int = 1) -> None:
        """Runs on a background thread. Each game's engine output is saved
        (and its puzzles generated) the moment it finishes, so stopping or
        closing the app mid-run loses at most the game in progress. UI
        updates go through the queue — never touch Tk from here."""
        total_moves = sum(analyzer.count_my_moves(g) for g in games)
        game_count = len(games)
        progress = {"moves": 0, "games": 0, "puzzles": 0}

        def progress_text() -> str:
            return (f"{progress['games']} of {game_count} games done · "
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
            analyzer.evaluate_games_parallel(
                games, stockfish_path, on_game_done,
                on_move_analyzed=on_move_analyzed, should_stop=self._stop_event.is_set,
                engines=engines,
            )
        except analyzer.AnalysisStopped:
            stopped = True

        finished, puzzle_count = progress["games"], progress["puzzles"]

        def on_done():
            self._hide_progress()
            self._set_busy(False)
            self._refresh_game_summary()
            self._reload_queue()
            self.after(500, self._classify_new_puzzles)        # the new puzzles' kinds
            prefix = "Stopped. " if stopped else ""
            messagebox.showinfo(
                "Analysis stopped" if stopped else "Analysis complete",
                f"{prefix}Analyzed and saved {finished} of {game_count} games, "
                f"found {puzzle_count} puzzles (blunder size {threshold_cp / 100:.1f} pawns).",
                parent=self,
            )

        self._ui_queue.put(on_done)

    # ---------- classifying new puzzles in the background (puzzle_kind.py) ----------

    def _classify_new_puzzles(self) -> None:
        """Works out kind + solution for puzzles that don't have them yet, on
        background threads; results are saved here on the UI thread. Never
        while an import / analysis is running (that pauses it)."""
        if self._busy or self._classify_stop is not None or not self.winfo_exists():
            return
        todo = puzzle_kind.unclassified(self.conn)
        path = engine_locator.find_stockfish()
        if not todo or path is None:
            return
        stop = threading.Event()
        self._classify_stop = stop
        engines = analyzer.PARALLEL_ENGINES if self.parallel_var.get() else 1
        self.classify_label.configure(text=f"Sorting {len(todo)} new puzzles into kinds…")

        def on_result(pid, result):
            self._ui_queue.put(lambda: self._save_kind(pid, result))

        def on_progress(done, total):
            if done % 10 == 0 or done == total:
                self._ui_queue.put(lambda: self.classify_label.configure(
                    text=f"Sorting new puzzles into kinds: {done} / {total}…"))

        def worker():
            try:
                puzzle_kind.classify_all(path, todo, on_result, engines=engines, stop=stop,
                                         on_progress=on_progress)
            except Exception:
                pass
            self._ui_queue.put(lambda: self._classify_finished(stop))

        threading.Thread(target=worker, daemon=True).start()

    def _save_kind(self, puzzle_id: str, result) -> None:
        try:
            puzzle_kind.save(self.conn, puzzle_id, result)
        except Exception:
            return                                     # e.g. rebuilt meanwhile: it'll be redone
        for p in self.queue:                           # the queue's copy too, without reloading it
            if p.id == puzzle_id:
                p.solution_ucis = " ".join(m.uci() for m in result.solution)
                p.solution_moves, p.kind = result.moves, result.kind

    def _classify_finished(self, stop: threading.Event) -> None:
        if self._classify_stop is stop:
            self._classify_stop = None
        left = len(puzzle_kind.unclassified(self.conn))
        self.classify_label.configure(text="" if not left else
                                      f"{left} puzzles still to sort into kinds (continues after the analysis).")
        if left and not stop.is_set():
            self.after(500, self._classify_new_puzzles)

    def _pause_classifying(self) -> None:
        if self._classify_stop is not None:
            self._classify_stop.set()
            self._classify_stop = None

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
        self.after(500, self._classify_new_puzzles)            # rebuilt puzzles: their kinds again
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
