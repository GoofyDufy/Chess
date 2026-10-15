"""Game Review: step through one of your analyzed games move by move, with
an evaluation graph, your mistakes marked, and Stockfish's better move
drawn as an arrow. Works entirely from the saved analysis (no engine run).

Only YOUR moves have evaluations (the cache stores the position before
each of your moves), so the graph has one point per move of yours, from
your point of view.

Analyze turns the board into a live analysis board from the move shown:
move either side, ◀ takes back (to the start of the game), ▶ plays on
along the game's moves (or a Stockfish line you clicked), with Stockfish's
top 3 lines as arrows (analysis_board.py, Stockfish on a background thread).
"""

from __future__ import annotations

import io
import queue
import sqlite3
import threading
import tkinter as tk
from typing import Dict, List, Optional

import chess
import chess.pgn
import customtkinter as ctk

import amazing
import db
import my_lines
import my_openings
import opening_library
import theme
from analyzer import MATE_CP, MIN_CHANCES_LOST, win_chances
from analysis_board import Analysis, IdeasPanel, TopMovesRow
from board_widget import BOARD_PIXELS, REPLY_COLOR, ChessBoardWidget
from eval_bar import EvalBar
from models import TIME_FILTERS, PlayerColor, in_time_filter, time_class
from repertoire_gaps import position_key

GRAPH_W, GRAPH_H = 380, 130
GRAPH_CLAMP = 600          # graph shows evals within +/-6 pawns
MARKS = [(300, "??", "Blunder", theme.ERROR), (150, "?", "Mistake", theme.WARNING),
         (80, "?!", "Inaccuracy", theme.INFO)]
# move badges on the board and colours in the moves list (one meaning per colour on this page:
# red / amber / blue are mistakes, so leaving your prep is purple, whoever did it)
AMAZING_COLOR = "#F472B6"  # !! an amazing move: a sound sacrifice (amazing.py)
BEST_COLOR = theme.SUCCESS
ONLY_COLOR = "#2DD4BF"     # ! the only move that keeps your position (every other is a mistake or worse)
BOOK_COLOR = "#C9CDD3"     # ≡ a move inside your prep
LEFT_PREP = "#8B5CF6"      # ⚑ the move that left your prep
PREP_BAND = "#23302A"      # the graph's stretch inside your prep


def _loss(ev) -> Optional[int]:
    if ev is None or ev["cp_after"] is None:
        return None
    return max(0, min(ev["cp_before"], MATE_CP) - max(ev["cp_after"], -MATE_CP))


def _mark(ev):
    """(symbol, label, color) for one of your moves, or None if it was fine."""
    loss = _loss(ev)
    if loss is None:
        return None
    for threshold, symbol, label, color in MARKS:
        if loss >= threshold:
            return symbol, label, color
    return None


def _pawns(cp: Optional[int]) -> str:
    if cp is None:
        return "?"
    if abs(cp) >= MATE_CP:
        return "mate" if cp > 0 else "−mate"
    return f"{cp / 100:+.1f}"


class GameReviewTab(ctk.CTkFrame):
    def __init__(self, master, conn):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.game_rows: List = []
        self.moves: List[chess.Move] = []
        self.sans: List[str] = []
        self.evals: Dict[int, object] = {}
        self.ply = 0                      # number of moves played on the shown board
        self.my_color = chess.WHITE
        self.prep: Optional[my_openings.PrepPath] = None      # where this game left your prep
        self._amazing: Dict[int, int] = {}                    # ply -> pawns' worth sacrificed (amazing moves)
        self._amazing_all: Optional[list] = None              # every amazing move of yours (the list)
        self._amazing_queue: "queue.Queue" = queue.Queue()
        self._library_positions = None                        # for naming a game's line (loaded once)
        self._ana_from_ply = 0                     # where Analyze was pressed (Back to game returns there)
        self._build()
        self.bind("<Map>", lambda e: self._refresh_games() if not self.game_rows else None)
        top = self.winfo_toplevel()
        top.bind("<Left>", lambda e: self._key(-1), add="+")
        top.bind("<Right>", lambda e: self._key(1), add="+")

    # ---------- layout ----------

    def _build(self) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=16, anchor="n")       # 16: room for Stockfish's lines at 768 px
        pad = ctk.CTkFrame(left, fg_color="transparent")
        pad.pack(padx=18, pady=12)
        self.title_label = theme.label(pad, "Pick a game on the right", "muted", wraplength=BOARD_PIXELS)
        self.title_label.pack(anchor="w")
        # the opening: its named line, and where it left your prep (and who left it)
        self.opening_label = theme.label(pad, "", "body", wraplength=BOARD_PIXELS + 26)
        self.opening_label.pack(anchor="w", pady=(2, 8))
        board_row = ctk.CTkFrame(pad, fg_color="transparent")
        board_row.pack()
        self.eval_bar = EvalBar(board_row, BOARD_PIXELS)
        self.eval_bar.pack(side="left", padx=(0, 8))
        self.board_widget = ChessBoardWidget(board_row, self._on_board_move, bg=theme.PANEL_BG)
        self.board_widget.pack(side="left")
        self.status_label = theme.label(pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(anchor="w", pady=(12, 0))
        self.detail_label = theme.label(pad, "", "body", wraplength=BOARD_PIXELS)
        self.detail_label.pack(anchor="w", pady=(2, 0))
        # analysis mode (Analyze, analysis_board.py): a board of its own from the move shown,
        # Stockfish's top lines a row each (shown while analysing)
        self.ideas_frame = IdeasPanel(pad, self._pick_idea)
        # clicking through with "Stockfish's top moves" on: one compact line (no room for more)
        self.top_moves_row = TopMovesRow(pad, self._pick_idea)
        self.analysis = Analysis(self, self.board_widget, self.ideas_frame, self.eval_bar,
                                 flipped=lambda: self.my_color == chess.BLACK, title=self._analysis_title,
                                 hint="click a line, then →", on_shown=self._on_analysis_shown,
                                 peek_panel=self.top_moves_row)
        nav = ctk.CTkFrame(pad, fg_color="transparent")
        nav.pack(fill="x", pady=(12, 0))
        self._nav_frame = nav
        for text, step in (("⏮", -999), ("←", -1), ("→", 1), ("⏭", 999)):
            theme.button(nav, text, lambda s=step: self._step(s), width=36).pack(side="left", padx=(0, 6))
        self.analyze_button = theme.button(nav, "Analyze", self._toggle_analysis, width=84)
        self.analyze_button.pack(side="left", padx=(2, 0))
        self.mistake_button = theme.button(nav, "Next mistake", self._next_mistake, "primary", width=112)
        self.mistake_button.pack(side="right")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=16)

        games_card = theme.card(right)
        games_card.pack(fill="x")                 # a fixed few rows: the moves list below gets the rest
        games = ctk.CTkFrame(games_card, fg_color="transparent", height=10)   # sized by its contents
        games.pack(fill="both", expand=True, padx=16, pady=14)
        head = ctk.CTkFrame(games, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        theme.label(head, "Analyzed games", "heading").pack(side="left")
        self.filter_var = tk.StringVar(value="All games")
        theme.option_menu(head, list(TIME_FILTERS), self.filter_var,
                          lambda _: self._refresh_games(), width=150).pack(side="right")
        box = ctk.CTkFrame(games, fg_color=theme.PANEL_ALT, corner_radius=10, height=10)
        box.pack(fill="both", expand=True)
        self.games_list = theme.listbox(box, height=5)
        scroll = ctk.CTkScrollbar(box, command=self.games_list.yview, button_color=theme.CONTROL_BG,
                                  fg_color=theme.PANEL_ALT, height=10)   # (default asks for 200 px)
        self.games_list.configure(yscrollcommand=scroll.set)
        self.games_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="left", fill="y", pady=8)
        self.games_list.bind("<<ListboxSelect>>", self._on_game_select)
        self.amazing_button = theme.button(games, "!! Your amazing moves", self._open_amazing, height=28,
                                           text_color=AMAZING_COLOR)
        self.amazing_button.pack(fill="x", pady=(8, 0))

        graph_card = theme.card(right)
        graph_card.pack(fill="x", pady=(14, 0))
        graph = ctk.CTkFrame(graph_card, fg_color="transparent")
        graph.pack(fill="x", padx=16, pady=12)
        graph_head = ctk.CTkFrame(graph, fg_color="transparent")
        graph_head.pack(fill="x", pady=(0, 6))
        theme.label(graph_head, "EVALUATION (YOUR POINT OF VIEW)", "section").pack(side="left")
        # Stockfish's top moves for each position as you click through (an option, remembered)
        self.top_moves_var = tk.BooleanVar(value=db.get_setting(self.conn, "review_top_moves") == "1")
        ctk.CTkCheckBox(graph_head, text="Stockfish's top moves", variable=self.top_moves_var,
                        command=self._on_top_moves_toggle, font=theme.FONT_SMALL, text_color=theme.TEXT_SOFT,
                        fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER_STRONG,
                        checkbox_width=16, checkbox_height=16).pack(side="right")
        self.graph = tk.Canvas(graph, width=GRAPH_W, height=GRAPH_H, bg=theme.PANEL_ALT,
                               highlightthickness=0)
        self.graph.pack(fill="x")
        self.graph.bind("<Button-1>", self._on_graph_click)

        moves_card = theme.card(right)
        moves_card.pack(fill="both", expand=True, pady=(14, 0))
        moves = ctk.CTkFrame(moves_card, fg_color="transparent")
        moves.pack(fill="both", expand=True, padx=16, pady=12)
        theme.label(moves, "MOVES", "section").pack(anchor="w")
        # in badge priority order (a move that's two things shows the first)
        for row_items in ((("≡ prep", BOOK_COLOR), ("⚑ left prep", "#A78BFA"), ("!! amazing", AMAZING_COLOR),
                           ("! only move", ONLY_COLOR), ("✓ best", BEST_COLOR)),
                          (("?! inaccuracy", theme.INFO), ("? mistake", theme.WARNING), ("?? blunder", theme.ERROR))):
            legend = ctk.CTkFrame(moves, fg_color="transparent")
            legend.pack(anchor="w")
            for text, color in row_items:
                theme.label(legend, text, "muted", text_color=color, height=16).pack(side="left", padx=(0, 10))
        mbox = ctk.CTkFrame(moves, fg_color=theme.PANEL_ALT, corner_radius=10)
        mbox.pack(fill="both", expand=True, pady=(6, 0))
        self.moves_list = theme.listbox(mbox, height=5)
        mscroll = ctk.CTkScrollbar(mbox, command=self.moves_list.yview, button_color=theme.CONTROL_BG,
                                   fg_color=theme.PANEL_ALT, height=10)
        self.moves_list.configure(yscrollcommand=mscroll.set)
        self.moves_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        mscroll.pack(side="left", fill="y", pady=8)
        self.moves_list.bind("<<ListboxSelect>>", self._on_move_select)

    # ---------- games list ----------

    def _refresh_games(self) -> None:
        blunders = {r["game_id"]: r["n"] for r in self.conn.execute(
            """SELECT game_id, SUM(cp_after IS NOT NULL AND cp_before - cp_after >= 300) AS n
               FROM move_evaluations GROUP BY game_id""")}
        classes = TIME_FILTERS[self.filter_var.get()]
        self.game_rows = [
            g for g in self.conn.execute(
                "SELECT * FROM games WHERE evaluated_at IS NOT NULL ORDER BY played_at DESC LIMIT 1500")
            if in_time_filter(g["time_control"], classes)
        ]
        self.games_list.delete(0, tk.END)
        for g in self.game_rows:
            date = g["played_at"][:10]
            n = blunders.get(g["id"], 0) or 0
            tag = f" · {n} blunder{'s' if n != 1 else ''}" if n else ""
            self.games_list.insert(tk.END, f"{date}  {g['result'].upper():<4} vs {g['opponent_username']}"
                                           f" · {time_class(g['time_control'])}{tag}")
        if not self.game_rows:
            self.title_label.configure(text="No analyzed games in this filter yet — run "
                                            "Analyze new games in Puzzle Review.")

    def _on_game_select(self, event=None) -> None:
        sel = self.games_list.curselection()
        if sel:
            self._load_game(self.game_rows[sel[0]])

    def open_game(self, game_id: str, fen: Optional[str] = None) -> None:
        """Called from Puzzle Review's 'Review game': load that game and jump
        to the puzzle position."""
        if not self.game_rows or all(g["id"] != game_id for g in self.game_rows):
            self.filter_var.set("All games")
            self._refresh_games()
        for i, g in enumerate(self.game_rows):
            if g["id"] == game_id:
                self.games_list.selection_clear(0, tk.END)
                self.games_list.selection_set(i)
                self.games_list.see(i)
                self._load_game(g)
                break
        else:
            # a game analyzed before results were saved: not in the list,
            # but its moves can still be stepped through
            row = self.conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                return
            self.games_list.selection_clear(0, tk.END)
            self._load_game(row)
        if fen:
            board = chess.Board()
            target = position_key(fen)
            for ply, move in enumerate(self.moves):
                if position_key(board.fen()) == target:
                    self._go(ply)
                    return
                board.push(move)

    def _load_game(self, g) -> None:
        self._stop_analysis()
        parsed = chess.pgn.read_game(io.StringIO(g["pgn"]))
        self.moves = list(parsed.mainline_moves()) if parsed else []
        board = chess.Board()
        self.sans = []
        for i, move in enumerate(self.moves):
            if move not in board.legal_moves:       # a damaged PGN: keep the moves up to there
                self.moves = self.moves[:i]
                break
            self.sans.append(board.san(move))
            board.push(move)
        self.evals = {r["ply"]: r for r in self.conn.execute(
            "SELECT * FROM move_evaluations WHERE game_id = ?", (g["id"],))}
        self.my_color = chess.WHITE if g["my_color"] == PlayerColor.WHITE.value else chess.BLACK
        self.prep = my_openings.prep_path(my_openings.repertoire_for(self.conn, g["my_color"]), self.moves,
                                          self.my_color)
        self._amazing = {}
        for ply, ev in self.evals.items():
            size = amazing.is_amazing(ev["fen_before"], ev["move_uci"], ev["cp_before"], ev["cp_after"])
            if size:
                self._amazing[ply] = size
        self.opening_label.configure(text=self._opening_text(g), text_color=self._prep_color())
        me = db.get_setting(self.conn, "chess_com_username") or "You"
        white, black = (me, g["opponent_username"]) if self.my_color == chess.WHITE else (g["opponent_username"], me)
        no_data = "" if self.evals else " · no saved analysis yet (re-analyze to see mistakes)"
        self.title_label.configure(text=f"{white} (White) vs {black} (Black) — {g['played_at'][:10]} "
                                        f"— {g['result']} · {time_class(g['time_control'])}{no_data}"
                                        + self._accuracy_text(g))

        self.moves_list.delete(0, tk.END)
        departure = self.prep.departure_ply if self.prep else None
        in_prep = self.prep.in_prep if self.prep else 0
        for ply, san in enumerate(self.sans):
            prefix = f"{ply // 2 + 1}. " if ply % 2 == 0 else f"{ply // 2 + 1}... "
            mark = _mark(self.evals.get(ply))
            book = "≡ " if ply < in_prep else "⚑ " if ply == departure else "   "     # ≡ inside your prep
            shown_mark = mark[0] if mark and ply >= in_prep and ply != departure else ""   # prep comes first
            label = f"{book}{prefix}{san}{shown_mark}"
            if ply == departure:
                label += "   left your prep" if self.prep.reason == "you" else "   they left your prep"
            elif mark and ply >= in_prep:
                label += f"   {mark[1]} ({_pawns(-_loss(self.evals[ply]))})"
            elif ply in self._amazing:
                label += "!!   amazing move"
            elif self._only_move(ply):
                label += "!   only move"
            self.moves_list.insert(tk.END, label)
            badge = self._badge(ply)
            if badge:
                self.moves_list.itemconfig(ply, foreground="#A78BFA" if badge[0] == "⚑" else badge[1])
            elif ply % 2 != (0 if self.my_color == chess.WHITE else 1):
                self.moves_list.itemconfig(ply, foreground=theme.TEXT_MUTED)
        self._draw_graph()
        self._go(0)

    # ---------- navigation ----------

    def _key(self, step: int) -> None:
        if not self.winfo_ismapped() or not self.moves:
            return
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "TEntry", "Text"):
            return
        self._step(step)

    def _step(self, step: int) -> None:
        """⏮ ← → ⏭ (and the arrow keys): along the game, or the analysis board when analysing."""
        if self.analysis.active:
            self.analysis.walk(step)
            return
        self._go(self.ply + step)

    def _go(self, ply: int) -> None:
        if not self.moves:
            return
        if self.analysis.active:             # a move or graph click: back to the game, there
            self._stop_analysis()
        self.ply = max(0, min(len(self.moves), ply))
        board = chess.Board()
        for move in self.moves[:self.ply]:
            board.push(move)
        self.board_widget.show_board(board, flipped=self.my_color == chess.BLACK)
        self._update_eval_bar()
        self._describe(board)
        badge = self._badge(self.ply - 1)
        if badge:
            self.board_widget.set_badges([(self.moves[self.ply - 1].to_square, *badge)])
        self._draw_graph()
        if self.ply > 0:
            self.moves_list.selection_clear(0, tk.END)
            self.moves_list.selection_set(self.ply - 1)
            self.moves_list.see(self.ply - 1)
        self._show_top_moves(board)

    # ---------- Stockfish's top moves while clicking through (an option) ----------

    def _on_top_moves_toggle(self) -> None:
        db.set_setting(self.conn, "review_top_moves", "1" if self.top_moves_var.get() else "0")
        if self.moves and not self.analysis.active:
            self._go(self.ply)

    def _show_top_moves(self, board: chess.Board) -> None:
        if not self.top_moves_var.get():
            self.analysis.unpeek()
            self.top_moves_row.pack_forget()
            return
        if not self.top_moves_row.winfo_manager():
            self.top_moves_row.pack(before=self._nav_frame, anchor="w", fill="x", pady=(6, 0))
        self.analysis.peek(board)

    def _pick_idea(self, i: int) -> None:
        """A line clicked: while clicking through the game, it starts Analyze here and plays it."""
        if not self.analysis.active:
            ideas = self.analysis.ideas
            if i >= len(ideas):
                return
            self._start_analysis()
            self.analysis.ideas = ideas
        self.analysis.follow(i)

    def _eval_at_ply(self) -> Optional[int]:
        """Best saved evaluation (YOUR point of view) for the shown position:
        the eval before your upcoming move, or after your last move, or the
        nearest earlier one. Only your moves are analyzed, so this is exact on
        your turn and carried over on your opponent's."""
        ev = self.evals.get(self.ply)
        if ev is not None:
            return ev["cp_before"]
        for ply in range(self.ply - 1, -1, -1):
            ev = self.evals.get(ply)
            if ev is not None:
                return ev["cp_after"] if ev["cp_after"] is not None else ev["cp_before"]
        return 0 if self.evals else None

    def _update_eval_bar(self) -> None:
        cp = self._eval_at_ply()
        if cp is not None and self.my_color == chess.BLACK:
            cp = -cp                                  # bar wants White's point of view
        self.eval_bar.set(cp, flipped=self.my_color == chess.BLACK)

    def _describe(self, board: chess.Board) -> None:
        """Explain the move just played and, when it's your turn, show the
        engine's best move as an arrow."""
        last = self.ply - 1
        ev = self.evals.get(last)
        if last < 0:
            self.status_label.configure(text="Start of the game.", text_color=theme.TEXT)
            self.detail_label.configure(text="Use ← / → or click a move.")
        elif ev is not None:
            mark = _mark(ev)
            best = chess.Board(ev["fen_before"]).san(chess.Move.from_uci(ev["best_move_uci"])) \
                if ev["best_move_uci"] else "?"
            if mark:
                self.status_label.configure(text=f"You played {self.sans[last]}{mark[0]} — {mark[1]}",
                                            text_color=mark[2])
                self.detail_label.configure(
                    text=f"Evaluation went from {_pawns(ev['cp_before'])} to {_pawns(ev['cp_after'])}. "
                         f"Best was {best} (arrow shown on the position before your move).")
            elif last in self._amazing:
                self.status_label.configure(text=f"You played {self.sans[last]}!! — an amazing move",
                                            text_color=AMAZING_COLOR)
                size = self._amazing[last]
                best = "the best move" if ev["move_uci"] == ev["best_move_uci"] else "nearly the best move"
                self.detail_label.configure(
                    text=f"You offered {size} pawns' worth of material — and it was {best} "
                         f"(Stockfish {_pawns(ev['cp_before'])} → {_pawns(ev['cp_after'])}).")
            elif self._only_move(last):
                board_before = chess.Board(ev["fen_before"])
                second = (board_before.san(chess.Move.from_uci(ev["second_move_uci"]))
                          if ev["second_move_uci"] else "anything else")
                self.status_label.configure(text=f"You played {self.sans[last]}! — the only move",
                                            text_color=ONLY_COLOR)
                cp = ev["cp_before"]
                keeps = ("keeps your advantage" if cp >= 50 else "holds the balance" if cp > -50
                         else "keeps you in the game")
                self.detail_label.configure(
                    text=f"It {keeps} ({_pawns(cp)}); the next best, {second}, would have given "
                         f"{_pawns(ev['cp_second'])}.")
            elif ev["move_uci"] == ev["best_move_uci"]:
                self.status_label.configure(text=f"You played {self.sans[last]} — best move",
                                            text_color=theme.SUCCESS)
                self.detail_label.configure(text=f"Evaluation {_pawns(ev['cp_before'])}.")
            else:
                self.status_label.configure(text=f"You played {self.sans[last]} — fine",
                                            text_color=theme.TEXT)
                self.detail_label.configure(
                    text=f"Evaluation {_pawns(ev['cp_before'])} → {_pawns(ev['cp_after'])}. "
                         f"Engine preferred {best}.")
        else:
            self.status_label.configure(text=f"Opponent played {self.sans[last]}", text_color=theme.TEXT)
            self.detail_label.configure(text="")
        self._describe_prep(last)
        # arrow: best move in the current position when it's your move
        upcoming = self.evals.get(self.ply)
        if upcoming is not None and upcoming["best_move_uci"] and not self.top_moves_var.get():
            move = chess.Move.from_uci(upcoming["best_move_uci"])
            self.board_widget.arrows = {(move.from_square, move.to_square)}
            self.board_widget.redraw()

    # ---------- analysis: walk through your own lines ----------

    def _on_board_move(self, move: chess.Move) -> None:
        """A move played on the board: in analysis, for either side; otherwise it starts analysis there."""
        if not self.moves:
            return
        if not self.analysis.active:
            self._start_analysis()
        self.analysis.play(move)

    def _toggle_analysis(self) -> None:
        if self.analysis.active:
            ply = self._ana_from_ply
            self._stop_analysis()
            self._go(ply)
        elif self.moves:
            self._start_analysis()

    def _start_analysis(self) -> None:
        """The analysis board from the move shown; ▶ plays on along the game."""
        self._ana_from_ply = self.ply
        self.analyze_button.configure(text="Back to game", width=112)
        self.mistake_button.pack_forget()
        self.top_moves_row.pack_forget()
        self.status_label.pack_forget()
        self.detail_label.pack_forget()
        self.ideas_frame.pack(before=self._nav_frame, anchor="w", fill="x", pady=(12, 0))
        board = chess.Board()
        for move in self.moves[:self.ply]:
            board.push(move)
        self.analysis.start(board, ahead=self.moves[self.ply:])

    def _stop_analysis(self) -> None:
        if not self.analysis.active:
            return
        self.analysis.stop()
        self.analysis.unpeek()
        self.analyze_button.configure(text="Analyze", width=84)
        self.mistake_button.pack(side="right")
        self.ideas_frame.pack_forget()
        self.status_label.pack(before=self._nav_frame, anchor="w", pady=(12, 0))
        self.detail_label.pack(before=self._nav_frame, anchor="w", pady=(2, 0))

    def _on_game_line(self, board: chess.Board) -> bool:
        stack = board.move_stack
        return stack == self.moves[:len(stack)]

    def _analysis_title(self, board: chess.Board) -> str:
        ply = len(board.move_stack)
        side = "White" if board.turn == chess.WHITE else "Black"
        where = f"move {ply // 2 + 1}" if ply else "start"
        return f"{side} to move · game, {where}" if self._on_game_line(board) else f"{side} to move · your line"

    def _on_analysis_shown(self, board: chess.Board) -> None:
        ply = len(board.move_stack)
        if self._on_game_line(board) and 0 < ply <= len(self.moves):
            self.moves_list.selection_clear(0, tk.END)
            self.moves_list.selection_set(ply - 1)
            self.moves_list.see(ply - 1)

    def _accuracy_text(self, g) -> str:
        """chess.com's accuracy (games reviewed there) and King Coach's, from the saved analysis."""
        parts = []
        mine = g["my_accuracy"] if "my_accuracy" in g.keys() else None
        theirs = g["opp_accuracy"] if "opp_accuracy" in g.keys() else None
        if mine is not None:
            parts.append(f"chess.com: you {mine:.1f}%" + (f", them {theirs:.1f}%" if theirs is not None else ""))
        ours = amazing.game_accuracy(self.evals)
        if ours is not None:
            parts.append(f"King Coach: you {ours:.1f}%")
        return ("\nAccuracy — " + " · ".join(parts)) if parts else ""

    def _open_amazing(self) -> None:
        """Every amazing move of yours (worked out once, in the background); pick one to see it."""
        if self._amazing_all is not None:
            self._show_amazing_list()
            return
        self.amazing_button.configure(text="!! Finding your amazing moves…", state="disabled")
        path = my_lines.db_path(self.conn)

        def worker():
            conn = sqlite3.connect(path, timeout=30)          # its own connection (saves what it finds)
            try:
                self._amazing_queue.put(amazing.find_all(conn))
            except Exception:
                self._amazing_queue.put([])
            finally:
                conn.close()

        threading.Thread(target=worker, daemon=True).start()
        self.after(200, self._poll_amazing)

    def _poll_amazing(self) -> None:
        try:
            self._amazing_all = self._amazing_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_amazing)
            return
        self.amazing_button.configure(text=f"!! Your amazing moves ({len(self._amazing_all)})", state="normal")
        self._show_amazing_list()

    def _show_amazing_list(self) -> None:
        moves = self._amazing_all or []
        games = {r["id"]: r for r in self.conn.execute(
            "SELECT id, played_at, opponent_username, result FROM games")}
        win = ctk.CTkToplevel(self)
        win.title("Your amazing moves")
        win.configure(fg_color=theme.BG)
        win.transient(self.winfo_toplevel())
        win.geometry("480x460")
        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=14)
        theme.label(body, f"!! Your amazing moves — {len(moves)}", "heading", text_color=AMAZING_COLOR).pack(anchor="w")
        theme.label(body, "Sound sacrifices: you offered a piece (2+ pawns' worth) with the best or nearly the best "
                          "move, when you weren't already winning. Double-click one to see it in Game Review.",
                    "muted", wraplength=440).pack(anchor="w", pady=(2, 8))
        box = theme.listbox(body, height=14)
        box.pack(fill="both", expand=True)
        marks = {"win": "W", "loss": "L", "draw": "D"}
        shown = [m for m in moves if m.game_id in games]
        for m in shown:
            g = games[m.game_id]
            number = f"{m.ply // 2 + 1}{'.' if m.ply % 2 == 0 else '...'}"
            box.insert(tk.END, f"  {g['played_at'][:10]}  {marks.get(g['result'], '?')}  {number}{m.san}!!  "
                               f"offered {m.offered} · vs {g['opponent_username']}")
        if not shown:
            box.insert(tk.END, "  None yet — analyze more games (Import & Analyze).")

        def open_selected(_=None):
            sel = box.curselection()
            if sel and sel[0] < len(shown):
                m = shown[sel[0]]
                win.destroy()
                self.open_game(m.game_id)
                self._go(m.ply + 1)          # on the move, its badge showing

        box.bind("<Double-Button-1>", open_selected)
        box.bind("<Return>", open_selected)
        theme.button(body, "Show it", open_selected, "primary", height=32, width=100).pack(anchor="w", pady=(10, 0))
        win.after(50, lambda: (win.lift(), box.focus_set()))

    def _only_move(self, ply: int) -> bool:
        """You played Stockfish's best, and every other move would have thrown away
        at least a mistake's worth of winning chances (Lichess's 0.2)."""
        ev = self.evals.get(ply)
        return (ev is not None and ev["best_move_uci"] is not None and ev["move_uci"] == ev["best_move_uci"]
                and ev["cp_second"] is not None
                and win_chances(ev["cp_before"]) - win_chances(ev["cp_second"]) >= MIN_CHANCES_LOST)

    def _badge(self, ply: int):
        """(text, colour) for the move at this ply, in priority order: your prep
        (≡ inside it, ⚑ the move that left it), !! amazing, ! only move, ✓ best, then
        ?! / ? / ?? — None when there's nothing to say."""
        if ply < 0 or ply >= len(self.moves):
            return None
        p = self.prep
        if p is not None and ply == p.departure_ply:
            return "⚑", LEFT_PREP
        if p is not None and ply < p.in_prep:
            return "≡", BOOK_COLOR
        if ply in self._amazing:
            return "!!", AMAZING_COLOR
        if self._only_move(ply):
            return "!", ONLY_COLOR
        ev = self.evals.get(ply)
        if ev is not None and ev["best_move_uci"] and ev["move_uci"] == ev["best_move_uci"]:
            return "✓", BEST_COLOR
        mark = _mark(ev)
        if mark:
            return mark[0], mark[2]
        return None

    # ---------- your opening prep in this game ----------

    def _prep_color(self) -> str:
        if self.prep is None:
            return theme.TEXT_SOFT
        return "#A78BFA" if self.prep.reason in ("you", "opponent") else theme.TEXT_SOFT

    @staticmethod
    def _number(ply: int) -> str:
        return f"{ply // 2 + 1}{'.' if ply % 2 == 0 else '...'}"

    def _move_name(self, ply: int) -> str:
        return self._number(ply) + self.sans[ply] if 0 <= ply < len(self.sans) else "?"

    def _opening_name(self, g) -> str:
        """The deepest named line (the opening library) this game reached."""
        row = self.conn.execute("SELECT library_id FROM game_lines WHERE game_id = ?", (g["id"],)).fetchone()
        lid = row[0] if row else None
        if row is None and opening_library.count(self.conn):
            if self._library_positions is None:
                self._library_positions = my_lines.library_positions(self.conn)
            lid, _ = my_lines.match_game(g["pgn"], self._library_positions)
        if lid is None:
            return ""
        hit = opening_library.get(self.conn, [lid])
        return hit[0].name if hit else ""

    def _opening_text(self, g) -> str:
        name = self._opening_name(g)
        p = self.prep
        if p is None:
            return name
        n = p.in_prep
        prep = self._number(n) + "/".join(p.prep_sans)
        if p.reason == "you":
            where = f"⚑ You left your prep with {self._move_name(n)} (your prep: {prep})"
        elif p.reason == "opponent":
            where = f"⚑ They left your prep with {self._move_name(n)} (your prep expects {prep})"
        elif p.reason == "ended":
            where = f"Your prep ran out after {self._move_name(n - 1)} — both sides stayed in it"
        elif p.reason == "still":
            where = "The whole opening stayed inside your prep"
        else:
            color = "White" if self.my_color == chess.WHITE else "Black"
            where = f"No prep for this opening as {color}"
        return (f"{name} · " if name else "") + where

    def _describe_prep(self, last: int) -> None:
        """On the move that left your prep: say so; on the position before it: your prep's move(s) as green arrows."""
        p = self.prep
        if p is None or p.departure_ply is None:
            return
        if last == p.departure_ply:
            who = "You left your prep here" if p.reason == "you" else "Your opponent left your prep here"
            expected = "your prep plays" if p.reason == "you" else "your prep expects"
            note = f"⚑ {who} — {expected} {'/'.join(p.prep_sans)}."
            current = self.detail_label.cget("text")
            self.detail_label.configure(text=note + (f"\n{current}" if current else ""))
        if self.ply == p.departure_ply:
            board = self.board_widget.board
            arrows = []
            for uci in p.prep_ucis:
                move = chess.Move.from_uci(uci)
                if move in board.legal_moves:
                    arrows.append((move.from_square, move.to_square, REPLY_COLOR))
            self.board_widget.set_marked_arrows(arrows)
            whose = "your move" if p.reason == "you" else "their move"
            self.detail_label.configure(text=f"Next, {whose} leaves your prep — green arrow: what your prep plays. "
                                             + self.detail_label.cget("text"))

    def _next_mistake(self) -> None:
        for ply in sorted(self.evals):
            if ply >= self.ply and _mark(self.evals[ply]):
                self._go(ply)     # position before the mistake, best-move arrow shown
                ev, mark = self.evals[ply], _mark(self.evals[ply])
                best = chess.Board(ev["fen_before"]).san(chess.Move.from_uci(ev["best_move_uci"]))
                self.status_label.configure(
                    text=f"Your move — in the game you played {self.sans[ply]}{mark[0]} ({mark[1]})",
                    text_color=mark[2])
                self.detail_label.configure(
                    text=f"Better was {best} (arrow). Evaluation {_pawns(ev['cp_before'])} would have "
                         f"become {_pawns(ev['cp_after'])} after your move. Press → to see it.")
                return
        self.status_label.configure(text="No more mistakes after this point.", text_color=theme.SUCCESS)

    def _on_move_select(self, event=None) -> None:
        sel = self.moves_list.curselection()
        if sel and sel[0] + 1 != self.ply:
            self._go(sel[0] + 1)

    # ---------- evaluation graph ----------

    def _graph_points(self):
        if not self.moves:
            return []
        n = max(len(self.moves), 1)
        points = []
        for ply in sorted(self.evals):
            cp = max(-GRAPH_CLAMP, min(GRAPH_CLAMP, self.evals[ply]["cp_before"]))
            x = 4 + (GRAPH_W - 8) * ply / n
            y = GRAPH_H / 2 - (GRAPH_H / 2 - 6) * cp / GRAPH_CLAMP
            points.append((ply, x, y))
        return points

    def _draw_graph(self) -> None:
        c = self.graph
        c.delete("all")
        width = c.winfo_width() if c.winfo_width() > 10 else GRAPH_W
        if self.moves and self.prep is not None and self.prep.in_prep:
            n = max(len(self.moves), 1)
            x_prep = (4 + (GRAPH_W - 8) * self.prep.in_prep / n) * (width / GRAPH_W)
            c.create_rectangle(0, 0, x_prep, GRAPH_H, fill=PREP_BAND, outline="")
            if self.prep.departure_ply is not None:
                c.create_line(x_prep, 0, x_prep, GRAPH_H, fill=self._prep_color(), width=2)
        c.create_line(0, GRAPH_H / 2, width, GRAPH_H / 2, fill=theme.BORDER_STRONG)
        points = self._graph_points()
        if len(points) >= 2:
            scale = width / GRAPH_W
            flat = [(4, GRAPH_H / 2)] + [(x * scale, y) for _, x, y in points] + [(points[-1][1] * scale, GRAPH_H / 2)]
            c.create_polygon(*[v for p in flat for v in p], fill="#2B3A30", outline="")
            c.create_line(*[v for _, x, y in points for v in (x * scale, y)], fill=theme.SUCCESS, width=2)
            for ply, x, y in points:
                mark = _mark(self.evals[ply])
                if mark:
                    c.create_oval(x * scale - 4, y - 4, x * scale + 4, y + 4, fill=mark[2], outline="")
        if self.moves:
            x = (4 + (GRAPH_W - 8) * self.ply / max(len(self.moves), 1)) * (width / GRAPH_W)
            c.create_line(x, 0, x, GRAPH_H, fill=theme.ACCENT, dash=(3, 3))
        c.create_text(6, 4, text=f"+{GRAPH_CLAMP // 100}", anchor="nw", fill=theme.TEXT_MUTED,
                      font=theme.FONT_NATIVE_BOLD)
        c.create_text(6, GRAPH_H - 4, text=f"−{GRAPH_CLAMP // 100}", anchor="sw",
                      fill=theme.TEXT_MUTED, font=theme.FONT_NATIVE_BOLD)

    def _on_graph_click(self, event) -> None:
        if not self.moves:
            return
        width = max(self.graph.winfo_width(), 10)
        ply = round((event.x / width * GRAPH_W - 4) / (GRAPH_W - 8) * len(self.moves))
        self._go(ply)
