"""Game Review: step through one of your analyzed games move by move, with
an evaluation graph, your mistakes marked, and Stockfish's better move
drawn as an arrow. Works entirely from the saved analysis (no engine run).

Only YOUR moves have evaluations (the cache stores the position before
each of your moves), so the graph has one point per move of yours, from
your point of view.
"""

from __future__ import annotations

import io
import tkinter as tk
from typing import Dict, List, Optional

import chess
import chess.pgn
import customtkinter as ctk

import db
import theme
from analyzer import MATE_CP
from board_widget import BOARD_PIXELS, ChessBoardWidget
from models import TIME_FILTERS, PlayerColor, in_time_filter, time_class
from repertoire_gaps import position_key

GRAPH_W, GRAPH_H = 380, 130
GRAPH_CLAMP = 600          # graph shows evals within +/-6 pawns
MARKS = [(300, "??", "Blunder", theme.ERROR), (150, "?", "Mistake", theme.WARNING),
         (80, "?!", "Inaccuracy", theme.INFO)]


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
        self._build()
        self.bind("<Map>", lambda e: self._refresh_games() if not self.game_rows else None)
        top = self.winfo_toplevel()
        top.bind("<Left>", lambda e: self._key(-1), add="+")
        top.bind("<Right>", lambda e: self._key(1), add="+")

    # ---------- layout ----------

    def _build(self) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        pad = ctk.CTkFrame(left, fg_color="transparent")
        pad.pack(padx=18, pady=16)
        self.title_label = theme.label(pad, "Pick a game on the right", "muted", wraplength=BOARD_PIXELS)
        self.title_label.pack(anchor="w", pady=(0, 8))
        self.board_widget = ChessBoardWidget(pad, lambda m: None, bg=theme.PANEL_BG)
        self.board_widget.pack()
        self.status_label = theme.label(pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(anchor="w", pady=(12, 0))
        self.detail_label = theme.label(pad, "", "body", wraplength=BOARD_PIXELS)
        self.detail_label.pack(anchor="w", pady=(2, 0))
        nav = ctk.CTkFrame(pad, fg_color="transparent")
        nav.pack(fill="x", pady=(12, 0))
        for text, step in (("⏮", -999), ("←", -1), ("→", 1), ("⏭", 999)):
            theme.button(nav, text, lambda s=step: self._go(self.ply + s), width=56).pack(side="left", padx=(0, 6))
        theme.button(nav, "Next mistake →", self._next_mistake, "primary", width=150).pack(side="right")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)

        games_card = theme.card(right)
        games_card.pack(fill="both", expand=True)
        games = ctk.CTkFrame(games_card, fg_color="transparent")
        games.pack(fill="both", expand=True, padx=16, pady=14)
        head = ctk.CTkFrame(games, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        theme.label(head, "Analyzed games", "heading").pack(side="left")
        self.filter_var = tk.StringVar(value="All games")
        theme.option_menu(head, list(TIME_FILTERS), self.filter_var,
                          lambda _: self._refresh_games(), width=150).pack(side="right")
        box = ctk.CTkFrame(games, fg_color=theme.PANEL_ALT, corner_radius=10)
        box.pack(fill="both", expand=True)
        self.games_list = theme.listbox(box, height=6)
        scroll = ctk.CTkScrollbar(box, command=self.games_list.yview, button_color=theme.CONTROL_BG,
                                  fg_color=theme.PANEL_ALT)
        self.games_list.configure(yscrollcommand=scroll.set)
        self.games_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="left", fill="y", pady=8)
        self.games_list.bind("<<ListboxSelect>>", self._on_game_select)

        graph_card = theme.card(right)
        graph_card.pack(fill="x", pady=(14, 0))
        graph = ctk.CTkFrame(graph_card, fg_color="transparent")
        graph.pack(fill="x", padx=16, pady=12)
        theme.label(graph, "EVALUATION (YOUR POINT OF VIEW)", "section").pack(anchor="w", pady=(0, 6))
        self.graph = tk.Canvas(graph, width=GRAPH_W, height=GRAPH_H, bg=theme.PANEL_ALT,
                               highlightthickness=0)
        self.graph.pack(fill="x")
        self.graph.bind("<Button-1>", self._on_graph_click)

        moves_card = theme.card(right)
        moves_card.pack(fill="both", expand=True, pady=(14, 0))
        moves = ctk.CTkFrame(moves_card, fg_color="transparent")
        moves.pack(fill="both", expand=True, padx=16, pady=12)
        theme.label(moves, "MOVES", "section").pack(anchor="w", pady=(0, 6))
        mbox = ctk.CTkFrame(moves, fg_color=theme.PANEL_ALT, corner_radius=10)
        mbox.pack(fill="both", expand=True)
        self.moves_list = theme.listbox(mbox, height=5)
        mscroll = ctk.CTkScrollbar(mbox, command=self.moves_list.yview, button_color=theme.CONTROL_BG,
                                   fg_color=theme.PANEL_ALT)
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
        parsed = chess.pgn.read_game(io.StringIO(g["pgn"]))
        self.moves = list(parsed.mainline_moves()) if parsed else []
        board = chess.Board()
        self.sans = []
        for move in self.moves:
            self.sans.append(board.san(move))
            board.push(move)
        self.evals = {r["ply"]: r for r in self.conn.execute(
            "SELECT * FROM move_evaluations WHERE game_id = ?", (g["id"],))}
        self.my_color = chess.WHITE if g["my_color"] == PlayerColor.WHITE.value else chess.BLACK
        me = db.get_setting(self.conn, "chess_com_username") or "You"
        white, black = (me, g["opponent_username"]) if self.my_color == chess.WHITE else (g["opponent_username"], me)
        no_data = "" if self.evals else " · no saved analysis yet (re-analyze to see mistakes)"
        self.title_label.configure(text=f"{white} (White) vs {black} (Black) — {g['played_at'][:10]} "
                                        f"— {g['result']} · {time_class(g['time_control'])}{no_data}")

        self.moves_list.delete(0, tk.END)
        for ply, san in enumerate(self.sans):
            prefix = f"{ply // 2 + 1}. " if ply % 2 == 0 else f"{ply // 2 + 1}... "
            mark = _mark(self.evals.get(ply))
            label = f"{prefix}{san}{mark[0] if mark else ''}"
            if mark:
                label += f"   {mark[1]} ({_pawns(-_loss(self.evals[ply]))})"
            self.moves_list.insert(tk.END, label)
            if mark:
                self.moves_list.itemconfig(ply, foreground=mark[2])
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
        self._go(self.ply + step)

    def _go(self, ply: int) -> None:
        if not self.moves:
            return
        self.ply = max(0, min(len(self.moves), ply))
        board = chess.Board()
        for move in self.moves[:self.ply]:
            board.push(move)
        self.board_widget.show_board(board, flipped=self.my_color == chess.BLACK)
        self._describe(board)
        self._draw_graph()
        if self.ply > 0:
            self.moves_list.selection_clear(0, tk.END)
            self.moves_list.selection_set(self.ply - 1)
            self.moves_list.see(self.ply - 1)

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
        # arrow: best move in the current position when it's your move
        upcoming = self.evals.get(self.ply)
        if upcoming is not None and upcoming["best_move_uci"]:
            move = chess.Move.from_uci(upcoming["best_move_uci"])
            self.board_widget.arrows = {(move.from_square, move.to_square)}
            self.board_widget.redraw()

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
