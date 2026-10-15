"""Opening Drill → Add lines → Opening library: every named opening from
Lichess's dataset (opening_library.py). Browse by family or search by name,
ECO code or moves; filter by whether it's in your repertoire and by
character (sharp / balanced / quiet); preview a line on the board with what
it leads to; add the ones you pick to a repertoire (as White or Black).

Each row starts with a flag: ✓ in your repertoire (green), ◐ partly (most of
its moves), ○ not yet. Each line is described as Sharp / Balanced / Quiet by
library_character.py — Stockfish works through the library once in the
background (~8 min, one engine) and a line you click goes first.

Threads: the download and the describer run on background threads; their
results come back through a queue polled with after() and are saved here,
on the main thread.
"""

from __future__ import annotations

import collections
import queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable, List, Optional

import chess
import chess.engine
import customtkinter as ctk

import engine_locator
import library_character
import my_lines
import opening_importer
import opening_library
import style
import theme
from board_widget import ChessBoardWidget
from my_games_view import pick_game_dialog

ALL = "All families"
SEARCH_DELAY_MS = 250
MANY = 40                  # adding more than this many lines at once asks first
LIMIT = 600                # lines listed at once
SAVE_EVERY = 25            # described lines saved in batches
SHOW_ALL, SHOW_IN, SHOW_OUT, SHOW_PLAYED = "All lines", "In my repertoire", "Not in my repertoire", "Played in my games"
BOOK, MOST, BEST, WORST = "Book order", "Most played", "Played best", "Played worst"
ANY_CHARACTER = "Any character"
FLAGS = {"in": "✓", "partly": "◐", "out": "○"}
CHARACTER_COLORS = {"Sharp": "#E8735E", "Balanced": "#E6A94A", "Quiet": "#8FB3E8"}


class LibraryWindow(ctk.CTkToplevel):
    def __init__(self, master, conn, ask_repertoire: Callable, on_added: Callable[[], None],
                 review_game: Optional[Callable] = None):
        super().__init__(master)
        self.conn = conn
        self.review_game = review_game            # (game_id, fen) -> Game Review
        self._recs: dict = {}                     # library id -> [my_lines.LineRecord] (your games)
        self._matching = False
        self.ask_repertoire = ask_repertoire      # (parent) -> (name, PlayerColor) or None
        self.on_added = on_added
        self.title("Opening library")
        self.configure(fg_color=theme.BG)
        self.geometry("1080x690")
        self.transient(master.winfo_toplevel())
        self._results: List[opening_library.Opening] = []
        self._families: List[str] = []
        self._mine: dict = {}
        self._queue: "queue.Queue" = queue.Queue()
        self._search_job = None
        self._current: Optional[opening_library.Opening] = None
        # the describer: a line you click jumps the queue
        self._stop = threading.Event()
        self._urgent: "collections.deque" = collections.deque()
        self._describing = False
        self._described_now = 0
        self._to_describe = 0
        self._pending: list = []
        try:
            self._style = style.compute_style(conn).style        # "sharp" / "quiet" / "balanced" / None
        except Exception:
            self._style = None
        self._build()
        self.bind("<Destroy>", lambda e: self._stop.set(), add="+")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self.lift)
        self.after(100, self._poll)
        if opening_library.count(conn):
            self._load()
        else:
            self._download()

    # ---------- layout ----------

    def _build(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x", pady=(0, 6))
        theme.label(head, "Opening library", "title").pack(side="left")
        self.search_var = tk.StringVar()
        entry = theme.entry(head, self.search_var, width=280)
        entry.pack(side="right")
        entry.bind("<KeyRelease>", lambda e: self._schedule_search())
        theme.label(head, "Search (name, ECO, or moves like e4 c5 Nf3)", "muted").pack(side="right", padx=(0, 8))

        filters = ctk.CTkFrame(body, fg_color="transparent")
        filters.pack(fill="x", pady=(0, 8))
        self.count_label = theme.label(filters, "", "muted")
        self.count_label.pack(side="left")
        self.character_var = tk.StringVar(value=ANY_CHARACTER)
        theme.option_menu(filters, [ANY_CHARACTER, "Sharp", "Balanced", "Quiet"], self.character_var,
                          lambda _: self._search(), width=150).pack(side="right")
        self.sort_var = tk.StringVar(value=BOOK)
        theme.option_menu(filters, [BOOK, MOST, BEST, WORST], self.sort_var,
                          lambda _: self._search(), width=140).pack(side="right", padx=(0, 8))
        self.show_var = tk.StringVar(value=SHOW_ALL)
        theme.option_menu(filters, [SHOW_ALL, SHOW_IN, SHOW_OUT, SHOW_PLAYED], self.show_var,
                          lambda _: self._search(), width=190).pack(side="right", padx=(0, 8))

        cols = ctk.CTkFrame(body, fg_color="transparent")
        cols.pack(fill="both", expand=True)

        # families
        fam = theme.card(cols)
        fam.pack(side="left", fill="y")
        fam_in = ctk.CTkFrame(fam, fg_color="transparent")
        fam_in.pack(fill="both", expand=True, padx=12, pady=12)
        theme.label(fam_in, "FAMILIES", "section").pack(anchor="w", pady=(0, 6))
        theme.label(fam_in, "Source: Lichess opening names (CC0)", "muted").pack(side="bottom", anchor="w", pady=(6, 0))
        self.family_list = theme.listbox(fam_in, width=22)
        fscroll = ctk.CTkScrollbar(fam_in, command=self.family_list.yview, button_color=theme.CONTROL_BG,
                                   fg_color=theme.PANEL_ALT)
        self.family_list.configure(yscrollcommand=fscroll.set)
        self.family_list.pack(side="left", fill="both", expand=True)
        fscroll.pack(side="left", fill="y")
        self.family_list.bind("<<ListboxSelect>>", lambda e: self._search())

        # lines
        lines = theme.card(cols)
        lines.pack(side="left", fill="both", expand=True, padx=12)
        lines_in = ctk.CTkFrame(lines, fg_color="transparent")
        lines_in.pack(fill="both", expand=True, padx=12, pady=12)
        theme.label(lines_in, "✓ in your repertoire · ◐ partly · ○ not yet · [your score in N games]",
                    "section").pack(anchor="w", pady=(0, 6))
        box = ctk.CTkFrame(lines_in, fg_color="transparent")
        box.pack(fill="both", expand=True)
        self.line_list = theme.listbox(box, selectmode="extended")
        lscroll = ctk.CTkScrollbar(box, command=self.line_list.yview, button_color=theme.CONTROL_BG,
                                   fg_color=theme.PANEL_ALT)
        self.line_list.configure(yscrollcommand=lscroll.set)
        self.line_list.pack(side="left", fill="both", expand=True)
        lscroll.pack(side="left", fill="y")
        self.line_list.bind("<<ListboxSelect>>", lambda e: self._preview())
        row = ctk.CTkFrame(lines_in, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        theme.button(row, "Select all shown", self._select_all, height=32, width=130).pack(side="left")
        self.add_button = theme.button(row, "Add picked to repertoire…", self._add, "primary", height=32, width=200)
        self.add_button.pack(side="right")

        # preview
        side = theme.card(cols)
        side.pack(side="left", fill="y")
        side_in = ctk.CTkFrame(side, fg_color="transparent")
        side_in.pack(fill="both", expand=True, padx=12, pady=12)
        self.board = ChessBoardWidget(side_in, lambda m: None, bg=theme.PANEL_BG)
        self.board.pack()
        # below the board: scrolls when a line has a lot to say (the window is as tall as the screen allows)
        side_in = ctk.CTkScrollableFrame(side_in, fg_color="transparent", width=340,
                                         scrollbar_button_color=theme.CONTROL_BG,
                                         scrollbar_button_hover_color=theme.BORDER_STRONG)
        side_in.pack(fill="both", expand=True)
        self.name_label = theme.label(side_in, "", "heading", wraplength=330)
        self.name_label.pack(anchor="w", pady=(6, 0))
        self.moves_label = theme.label(side_in, "", "muted", wraplength=330)
        self.moves_label.pack(anchor="w", pady=(2, 0))
        self.flag_label = ctk.CTkLabel(side_in, text="", height=26, corner_radius=8, font=theme.FONT_BOLD,
                                       fg_color=theme.PANEL_ALT, text_color=theme.TEXT_SOFT)
        self.flag_label.pack(anchor="w", pady=(8, 0))
        self.add_one_button = theme.button(side_in, "Add this line…", self._add_current, height=28, width=130)
        self.character_label = theme.label(side_in, "", "status")
        self.character_label.pack(anchor="w", pady=(6, 0))
        self.description_label = theme.label(side_in, "", "body", wraplength=330)
        self.description_label.pack(anchor="w", pady=(2, 0))
        theme.label(side_in, "YOUR GAMES", "section").pack(anchor="w", pady=(10, 2))
        self.games_label = theme.label(side_in, "", "body", wraplength=330)
        self.games_label.pack(anchor="w")
        self.review_button = theme.button(side_in, "Review these games…", self._review_games, height=28, width=160)

    # ---------- data ----------

    def _download(self) -> None:
        self.count_label.configure(text="Downloading the library from Lichess…")

        def worker():
            try:
                self._queue.put(("rows", opening_library.download()))
            except Exception as e:
                self._queue.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        changed = False
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "rows":
                    opening_library.save(self.conn, value)
                    self._load()
                elif kind == "described":
                    self._pending.append(value)
                    self._described_now += 1
                    changed = True
                    if self._current is not None and value[0] == self._current.id:
                        self._apply_character(self._current, value[1])
                elif kind == "matched":
                    self._matching = False
                    if value:
                        my_lines.save(self.conn, value)
                        self._recs = my_lines.by_library_id(my_lines.records(self.conn))
                        self._search()
                        if self._current is not None:
                            self._show_games(self._current)
                elif kind == "describe done":
                    self._describing = False
                    self._flush()
                    self._search()
                else:
                    self.count_label.configure(text=f"Couldn't download the library ({type(value).__name__}) — "
                                                    "check the internet connection and open it again.")
        except queue.Empty:
            pass
        if changed and len(self._pending) >= SAVE_EVERY:
            self._flush()
        if self._describing:
            self._update_count()
        self.after(150, self._poll)

    def _flush(self) -> None:
        if self._pending:
            opening_library.save_character(self.conn, self._pending, library_character.VERSION)
            self._pending = []

    def _load(self) -> None:
        fams = opening_library.families(self.conn)
        self._families = [ALL] + [f for f, _ in fams]
        self.family_list.delete(0, tk.END)
        self.family_list.insert(tk.END, f"  {ALL}")
        for f, n in fams:
            self.family_list.insert(tk.END, f"  {f} ({n})")
        self.family_list.selection_set(0)
        self._mine = {c: opening_library.repertoire_moves(self.conn, c) for c in ("white", "black")}
        self._recs = my_lines.by_library_id(my_lines.records(self.conn))
        self._search()
        self._start_describing()
        self._start_matching()

    # ---------- your games, matched to the library (my_lines.py) ----------

    def _start_matching(self) -> None:
        todo = my_lines.unmatched(self.conn)
        if not todo or self._matching:
            return
        self._matching = True
        path = my_lines.db_path(self.conn)

        def worker():
            try:
                self._queue.put(("matched", my_lines.match_all(path, todo)))
            except Exception:
                self._queue.put(("matched", []))

        threading.Thread(target=worker, daemon=True).start()

    def _close(self) -> None:
        self._stop.set()
        self._flush()                    # keep what's been described so far
        self.destroy()

    # ---------- the describer (background) ----------

    def _start_describing(self) -> None:
        todo = opening_library.undescribed(self.conn, library_character.VERSION)
        path = engine_locator.find_stockfish()
        if not todo or path is None or self._describing:
            return
        self._describing, self._to_describe, self._described_now = True, len(todo), 0
        by_id = {o.id: o for o in todo}
        order = collections.deque(o.id for o in todo)

        def worker():
            done = set()
            try:
                with chess.engine.SimpleEngine.popen_uci(path) as engine:
                    while not self._stop.is_set():
                        oid = None
                        while self._urgent:                 # a line you clicked first
                            cand = self._urgent.popleft()
                            if cand in by_id and cand not in done:
                                oid = cand
                                break
                        while oid is None and order:
                            cand = order.popleft()
                            if cand not in done:
                                oid = cand
                        if oid is None:
                            break
                        done.add(oid)
                        self._queue.put(("described", (oid, library_character.describe(engine, by_id[oid]))))
            except Exception:
                pass
            self._queue.put(("describe done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _update_count(self, base: Optional[str] = None) -> None:
        text = base or getattr(self, "_count_text", "")
        if self._matching:
            text += "  ·  matching your games to the lines…"
        if self._describing:
            text += f"  ·  Stockfish is describing the lines: {self._described_now:,}/{self._to_describe:,}"
        self.count_label.configure(text=text)

    # ---------- the list ----------

    def _schedule_search(self) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(SEARCH_DELAY_MS, self._search)

    def _search(self) -> None:
        self._search_job = None
        self._flush()                                   # described lines so far, so filters see them
        sel = self.family_list.curselection()
        family = self._families[sel[0]] if sel and self._families and sel[0] > 0 else None
        character = self.character_var.get() if self.character_var.get() != ANY_CHARACTER else None
        found = opening_library.search(self.conn, self.search_var.get(), family, limit=10_000, character=character)
        show = self.show_var.get()
        rows = []
        for o in found:
            flag, color, n = opening_library.status(o, self._mine)
            if (show == SHOW_IN and flag != "in") or (show == SHOW_OUT and flag == "in"):
                continue
            if show == SHOW_PLAYED and o.id not in self._recs:
                continue
            rows.append((o, flag))
        rows = self._sorted(rows)[:LIMIT]
        self._results = [o for o, _ in rows]
        self.line_list.delete(0, tk.END)
        # all one family: show just the variation, so the part that differs isn't cut off
        one_family = len({o.family for o in self._results}) == 1
        for i, (o, flag) in enumerate(rows):
            label = (o.variation or o.name) if one_family else o.name
            char = f"  · {o.character}" if o.character else ""
            recs = self._recs.get(o.id)
            you = ""
            if recs:                                    # up front: long names would hide it at the end
                games = sum(r.games for r in recs)
                points = sum(r.record.wins + 0.5 * r.record.draws for r in recs)
                you = f"[{points / games:.0%} in {games}]  "
            self.line_list.insert(tk.END, f" {FLAGS[flag]} {you}{o.eco}  {label} · {o.last_move}{char}")
            if flag == "in":
                self.line_list.itemconfig(i, foreground=theme.SUCCESS)
        if not rows:
            self.line_list.insert(tk.END, "  Nothing matches — try fewer words, All families, or other filters.")
        total = opening_library.count(self.conn)
        shown = (f"{len(rows)} line{'s' if len(rows) != 1 else ''}"
                 + (f" (the first {LIMIT} — search or pick a family to narrow it)" if len(rows) >= LIMIT else "")
                 + (f" · {family}" if family else ""))
        self._count_text = f"{total:,} named openings · showing {shown}"
        self._update_count()

    def _sorted(self, rows):
        """Book order, or by your games: most played / played best / played worst
        (best and worst rank lines with MIN_GAMES+ games, by a score that doesn't
        over-trust a few games, then by opening accuracy)."""
        sort = self.sort_var.get()
        if sort == BOOK:
            return rows

        def top(o):
            recs = self._recs.get(o.id) or []
            return max(recs, key=lambda r: r.games) if recs else None

        if sort == MOST:
            return sorted(rows, key=lambda row: -sum(r.games for r in self._recs.get(row[0].id, [])))
        sign = -1 if sort == BEST else 1

        def key(row):
            rec = top(row[0])
            if rec is None or rec.games < my_lines.MIN_GAMES:
                return (1, 0, 0)
            loss = rec.avg_loss if rec.avg_loss is not None else 0.5
            return (0, sign * rec.rating, -sign * loss)
        return sorted(rows, key=key)

    def _selected(self) -> List[opening_library.Opening]:
        return [self._results[i] for i in self.line_list.curselection() if i < len(self._results)]

    # ---------- the preview ----------

    def _preview(self) -> None:
        picked = self._selected()
        if not picked:
            return
        o = picked[-1]
        self._current = o
        board = chess.Board()
        for uci in o.ucis:
            board.push_uci(uci)
        # from the side that plays the last move (that's usually whose opening it is)
        self.board.show_board(board, flipped=board.turn == chess.WHITE)
        self.name_label.configure(text=f"{o.eco} · {o.name}")
        self.moves_label.configure(text=o.pgn + (f"   ({len(picked)} lines picked)" if len(picked) > 1 else ""))
        self._show_flag(o)
        self._show_games(o)
        if o.character:
            self._show_character(o.character, o.description, o.eval_cp)
        else:
            self.character_label.configure(text="Describing…", text_color=theme.TEXT_MUTED)
            self.description_label.configure(text="Stockfish is looking at where this line leads.")
            self._urgent.appendleft(o.id)
            if not self._describing:
                self._start_describing()

    def _show_games(self, o: opening_library.Opening) -> None:
        recs = self._recs.get(o.id) or []
        if recs:
            self.games_label.configure(text="\n".join(my_lines.describe(r) for r in recs))
            if self.review_game:
                self.review_button.pack(anchor="w", pady=(6, 0))
        else:
            self.games_label.configure(text="None of your games reached this line as its deepest named one."
                                       if not self._matching else "Matching your games to the lines…")
            self.review_button.pack_forget()

    def _review_games(self) -> None:
        o = self._current
        recs = self._recs.get(o.id) if o is not None else None
        if not recs or self.review_game is None:
            return
        board = chess.Board()
        for uci in o.ucis:
            board.push_uci(uci)
        ids = [g for r in recs for g in r.game_ids]
        pick_game_dialog(self, self.conn, ids, o.name, board.fen(), self.review_game)

    def _show_flag(self, o: opening_library.Opening) -> None:
        flag, color, n = opening_library.status(o, self._mine)
        if flag == "in":
            self.flag_label.configure(text=f"  ✓ In your {color} repertoire  ", fg_color="#1F3A2B",
                                      text_color=theme.SUCCESS)
            self.add_one_button.pack_forget()
        else:
            text = (f"  ◐ Partly in your {color} repertoire ({n}/{o.plies})  " if flag == "partly"
                    else "  ○ Not in your repertoire  ")
            self.flag_label.configure(text=text, fg_color=theme.PANEL_ALT, text_color=theme.TEXT_SOFT)
            self.add_one_button.pack(after=self.flag_label, anchor="w", pady=(6, 0))

    def _show_character(self, label: str, description: Optional[str], eval_cp: Optional[int]) -> None:
        evaluation = f"   Stockfish {eval_cp / 100:+.2f}".replace("-", "−") if eval_cp is not None else ""
        self.character_label.configure(text=f"{label}{evaluation}", text_color=CHARACTER_COLORS.get(label, theme.TEXT))
        fit = ""
        if self._style in ("sharp", "quiet"):
            mine = "Sharp" if self._style == "sharp" else "Quiet"
            if label == mine:
                fit = f"\nFits your style — you score better in {self._style} positions (Stats)."
            elif label in ("Sharp", "Quiet"):
                fit = f"\nAgainst your usual style — you score better in {self._style} positions (Stats)."
        self.description_label.configure(text=(description or "") + fit)

    def _apply_character(self, o: opening_library.Opening, ch) -> None:
        o.character, o.description, o.eval_cp = ch.label, ch.text, ch.eval_cp
        self._show_character(ch.label, ch.text, ch.eval_cp)

    def _select_all(self) -> None:
        if self._results:
            self.line_list.selection_set(0, tk.END)
            self._preview()

    # ---------- adding ----------

    def _add_current(self) -> None:
        if self._current is not None:
            self._add([self._current])

    def _add(self, picked: Optional[List[opening_library.Opening]] = None) -> None:
        picked = picked or self._selected()
        if not picked:
            self.description_label.configure(text="Pick one or more lines first.")
            return
        if len(picked) > MANY and not messagebox.askyesno(
                "Opening library", f"Add all {len(picked)} lines? Big repertoires are harder to drill — "
                                   "you can turn lines off later (Turn lines on/off).", parent=self):
            return
        answer = self.ask_repertoire(self)
        if not answer:
            return
        name, color = answer
        added = opening_importer.import_pgn_text(opening_library.to_pgn(picked), name, color, self.conn)
        self._mine = {c: opening_library.repertoire_moves(self.conn, c) for c in ("white", "black")}
        self.on_added()
        keep = self._current
        self._search()
        if keep is not None:
            self._show_flag(keep)
        self.moves_label.configure(
            text=f"Added {len(picked)} line{'s' if len(picked) != 1 else ''} to '{name}' ({color.value}) — "
                 f"{added} new move{'s' if added != 1 else ''}. Drill them from Setup.")
