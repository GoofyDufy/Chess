"""Opening Drill → My games: the openings you play most (from your games),
where you leave your own prep, and why that's worse (my_openings.py,
opening_commentary.py). Selecting a deviation shows it on the big board:
green arrow = your prep move, red = what you played.

The drill tab passes:
  show_position(board, mine, prep, title, status) — put it on its board
  show_on_tree(deviation) — open the Tree at that position
Commentary is worked out by Stockfish on a background thread the first
time (~1 s) and saved; results come back through a queue polled with after().
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from typing import Callable, List, Optional

import chess
import chess.engine
import customtkinter as ctk

import engine_locator
import explorer
import my_openings
import opening_commentary
import practical
import theme

EVEN_CP = 15                   # under this, Stockfish calls the two moves even (opening_commentary)
MIN_RESULT_GAMES = 10          # games each before comparing your results with Stockfish's verdict
REFRESH_SECONDS = 300          # the games report is recomputed at most this often (or after new games)
ALL = "All openings"


class MyGamesView(ctk.CTkFrame):
    def __init__(self, master, conn, show_position: Callable, show_on_tree: Callable,
                 review_game: Optional[Callable] = None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.show_position = show_position
        self.show_on_tree = show_on_tree
        self.review_game = review_game          # (game_id, fen) -> Game Review at that position
        self._report: Optional[my_openings.Report] = None
        self._report_at = 0.0
        self._report_games = -1
        self._openings: List[my_openings.OpeningUse] = []
        self._shown: List[my_openings.MyDeviation] = []
        self._current: Optional[my_openings.MyDeviation] = None
        self._results: "queue.Queue" = queue.Queue()
        self._batch_running = False
        self._stop = threading.Event()          # set when the window closes (the worker never touches Tk)
        self._costs = {}               # deviation key -> centipawns it costs (from saved commentary)
        self._practical = {}           # deviation key -> [practical.Choice], or "busy" / an error text
        self._notes_for = {}           # deviation key -> its Stockfish commentary, once known
        self._build()
        self.bind("<Destroy>", lambda e: self._stop.set(), add="+")
        self.after(150, self._poll)

    # ---------- layout ----------

    def _build(self) -> None:
        card = theme.card(self)
        card.pack(fill="both", expand=True)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=12)
        theme.fit_wrap(body)
        self.summary = theme.label(body, "", "muted", wraplength=380)
        self.summary.pack(anchor="w", fill="x")

        theme.label(body, "YOUR MOST PLAYED OPENINGS", "section").pack(anchor="w", pady=(8, 4))
        self.openings_list = theme.listbox(body, height=4)
        self.openings_list.pack(fill="x")
        self.openings_list.bind("<<ListboxSelect>>", lambda e: self._on_opening())

        theme.label(body, "WHERE YOU LEAVE YOUR PREP", "section").pack(anchor="w", pady=(10, 4))
        self.dev_list = theme.listbox(body, height=4)
        self.dev_list.pack(fill="x")
        self.dev_list.bind("<<ListboxSelect>>", lambda e: self._on_deviation())

        self.text = ctk.CTkTextbox(body, height=150, wrap="word", font=theme.FONT_BASE, fg_color=theme.PANEL_ALT,
                                   text_color=theme.TEXT, corner_radius=8, border_width=0)
        self.text.pack(fill="both", expand=True, pady=(10, 6))
        self.text.configure(state="disabled")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.tree_button = theme.button(row, "Show on the tree", self._on_tree, height=32, width=130)
        self.tree_button.pack(side="left")
        self.games_button = theme.button(row, "Review games…", self._pick_game, height=32, width=120)
        self.games_button.pack(side="left", padx=(6, 0))
        self.analyze_button = theme.button(row, "Analyze", self._analyze, height=32, width=80)
        self.analyze_button.pack(side="left", padx=(6, 0))
        for b in (self.tree_button, self.games_button, self.analyze_button):
            b.configure(state="disabled")

    def _set_text(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    # ---------- data ----------

    def refresh(self) -> None:
        """Called when the view is shown: recompute after new games or every few minutes."""
        games = self.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        if self._report is None or games != self._report_games or time.time() - self._report_at > REFRESH_SECONDS:
            self._report = my_openings.compute(self.conn)
            self._report_at, self._report_games = time.time(), games
        r = self._report
        left = sum(o.left for o in r.openings)
        self.summary.configure(text=f"From your {r.games} games: {len(r.deviations)} places where you "
                                    f"left your own prep ({left} games). Pick one to see why it matters.")
        self._openings = [o for o in r.openings if o.games >= 3][:30]
        self.openings_list.delete(0, tk.END)
        self.openings_list.insert(tk.END, f"  {ALL}")
        for o in self._openings:
            prep = (f"left prep {o.left}×" if o.left else "in prep" if o.followed else "no prep")
            self.openings_list.insert(tk.END, f"  {o.name} ({o.color[0].upper()}) {o.games} · "
                                              f"{o.record.score:.0%} · {prep}")
        if not self.openings_list.curselection():
            self.openings_list.selection_set(0)
        self._load_costs()
        self._on_opening()
        self._start_batch()

    def deviation_for(self, fen: str) -> List[my_openings.MyDeviation]:
        """Your deviations at this position (for the Tree's badges)."""
        if self._report is None:
            self.refresh()
        key = my_openings.position_key(fen)
        return [d for d in self._report.deviations if my_openings.position_key(d.fen) == key]

    def all_opponent_deviations(self) -> List[my_openings.OpponentDeviation]:
        if self._report is None:
            self.refresh()
        return self._report.opponent_deviations

    def all_deviations(self) -> List[my_openings.MyDeviation]:
        if self._report is None:
            self.refresh()
        return self._report.deviations

    def _load_costs(self) -> None:
        for d in self._report.deviations:
            n = opening_commentary.cached(self.conn, d.fen, d.played_uci, d.prep_ucis)
            if n is not None:
                self._costs[d.key] = n.cost

    def _weight(self, d: my_openings.MyDeviation) -> float:
        """What a deviation costs you overall: how much worse x how often (unknown cost: by games)."""
        cost = self._costs.get(d.key)
        return d.games * max(cost - EVEN_CP, 0) if cost is not None else -1.0 / (1 + d.games)

    def _row(self, d: my_openings.MyDeviation) -> str:
        cost = self._costs.get(d.key)
        sf = ("" if cost is None else " · even" if abs(cost) < 15 else
              f" · better +{-cost / 100:.2f}" if cost < 0 else f" · −{cost / 100:.2f}")
        return f"  {d.line} (prep {'/'.join(d.prep_sans)}) {d.games}×{sf}"

    def _on_opening(self) -> None:
        sel = self.openings_list.curselection()
        devs = self._report.deviations if self._report else []
        if sel and sel[0] > 0:
            o = self._openings[sel[0] - 1]
            devs = [d for d in devs if d.key in o.deviation_keys]
        # what costs you most first (once Stockfish has looked), then the rest by how often
        devs = sorted(devs, key=lambda d: (-self._weight(d), -d.games))
        self._shown = devs
        self.dev_list.delete(0, tk.END)
        for d in devs:
            self.dev_list.insert(tk.END, self._row(d))
        if not devs:
            self.dev_list.insert(tk.END, "  You always followed your prep here." if sel and sel[0] > 0
                                 else "  No deviations yet — or no repertoire for these games.")

    def open(self, deviation: my_openings.MyDeviation) -> None:
        """Show this deviation (from the Tree's badge)."""
        self.openings_list.selection_clear(0, tk.END)
        self.openings_list.selection_set(0)
        self._on_opening()
        if deviation in self._shown:
            i = self._shown.index(deviation)
            self.dev_list.selection_clear(0, tk.END)
            self.dev_list.selection_set(i)
            self.dev_list.see(i)
        self._show(deviation)

    def _on_deviation(self) -> None:
        sel = self.dev_list.curselection()
        if sel and sel[0] < len(self._shown):
            self._show(self._shown[sel[0]])

    def _board(self, d: my_openings.MyDeviation) -> chess.Board:
        board = chess.Board()
        for san in d.prefix:
            board.push_san(san)
        return board

    def _show(self, d: my_openings.MyDeviation) -> None:
        self._current = d
        for b in (self.tree_button, self.analyze_button):
            b.configure(state="normal")
        self.games_button.configure(state="normal" if self.review_game else "disabled")
        board = self._board(d)
        mine = chess.Move.from_uci(d.played_uci)
        prep = [chess.Move.from_uci(u) for u in d.prep_ucis]
        self.show_position(board, mine, prep, f"Move {d.move_number}: you play {d.played_san}",
                           f"Your prep: {'/'.join(d.prep_sans)} (green) · you played {d.played_san} (red), "
                           f"{d.games} time{'s' if d.games != 1 else ''}.")
        self._request_practical(d)
        notes = opening_commentary.cached(self.conn, d.fen, d.played_uci, d.prep_ucis)
        if notes is not None:
            self._notes_for[d.key] = notes
            self._set_text(self._compose(d, notes))
            return
        self._set_text(self._compose(d, None) + "\n\nStockfish is looking at both moves…")
        path = engine_locator.find_stockfish()
        if path is None:
            self._set_text(self._compose(d, None) + "\n\nStockfish isn't set up — see Import & Analyze.")
            return

        def worker():
            try:
                with chess.engine.SimpleEngine.popen_uci(path) as engine:
                    n = opening_commentary.compare(engine, board, mine, prep)
                self._results.put((d, n, None))
            except Exception as e:
                self._results.put((d, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _compose(self, d: my_openings.MyDeviation, notes) -> str:
        parts = []
        if notes is not None:
            parts.append(opening_commentary.text(notes))
        # your own games
        mine = d.record
        line = (f"In your games: with {d.played_san} here you scored {mine.score:.0%} "
                f"({mine.wins}W {mine.draws}D {mine.losses}L in {mine.games})")
        prep = d.prep_record
        if prep.games:
            line += f"; with your prep move {prep.score:.0%} ({prep.games} games)."
        else:
            line += "; you've never actually played your prep move here."
        if notes is not None and notes.cost >= 40 and mine.games >= MIN_RESULT_GAMES \
                and prep.games >= MIN_RESULT_GAMES and mine.score >= prep.score - 0.02:
            line += (f" So far it hasn't hurt your results — at your level Stockfish's edge often doesn't show, "
                     f"but against stronger players {notes.mine_san} gives them an easier game.")
        elif notes is not None and notes.cost >= 40 and mine.games >= MIN_RESULT_GAMES \
                and prep.games >= MIN_RESULT_GAMES and mine.score < prep.score - 0.05:
            line += " Your results agree with Stockfish: you do better with your prep move."
        parts.append(line)
        # Lichess players at your level (cached explorer data only)
        stats = explorer.cached(self.conn, d.fen)
        if stats is not None and stats.games:
            board = self._board(d)
            by_uci = {m.uci: m for m in stats.moves}
            bits = []
            for uci in d.prep_ucis + [d.played_uci]:
                m = by_uci.get(uci)
                if m is not None and m.games:
                    bits.append(f"{board.san(chess.Move.from_uci(uci))} is played {m.games / stats.games:.0%} "
                                f"of the time and scores {m.score_for(board.turn):.0%}")
            if bits:
                band = explorer.describe_bands(explorer.rating_bands(self.conn))
                parts.append(f"Lichess players {band}: " + "; ".join(bits) + ".")
        if d.repertoires:
            parts.append(f"Your prep here comes from: {', '.join(d.repertoires)}.")
        got = self._practical.get(d.key)
        if isinstance(got, list):
            parts.append(practical.describe(got, explorer.describe_bands(explorer.rating_bands(self.conn))))
        elif got == "busy":
            parts.append("Checking what works against players at your level here (Lichess + Stockfish)…")
        elif got:
            parts.append(got)
        return "\n\n".join(parts)

    # ---------- what works at your level (practical.py) ----------

    def _request_practical(self, d: my_openings.MyDeviation) -> None:
        bands = explorer.rating_bands(self.conn)
        if d.key in self._practical and self._practical[d.key] != "busy":
            self._show_practical_arrow(d)
            return
        hit = practical.cached(self.conn, bands, d.fen, d.played_uci)
        if hit is not None:
            self._practical[d.key] = hit
            self._show_practical_arrow(d)
            return
        token = explorer.get_token(self.conn)
        path = engine_locator.find_stockfish()
        if not token:
            self._practical[d.key] = ("Connect Lichess (Add lines → Connect Lichess...) to see which moves work "
                                      "best against players at your level here.")
            return
        if path is None or self._practical.get(d.key) == "busy":
            return
        self._practical[d.key] = "busy"
        board = self._board(d)
        # read the cache here (the worker never touches the database)
        stats = explorer.cached(self.conn, d.fen, bands)
        after_cached = {}
        if stats is not None:
            for uci in practical.candidates(board, stats, d.prep_ucis, d.played_uci):
                after = board.copy()
                after.push(chess.Move.from_uci(uci))
                after_cached[uci] = explorer.cached(self.conn, after.fen(), bands)

        def fetch(fen, fetched):
            time.sleep(explorer.REQUEST_GAP_S)
            try:
                data = explorer.fetch_stats(token, fen, bands)
            except explorer.ExplorerError as e:
                if str(e) != "rate limited":
                    raise
                time.sleep(explorer.RATE_LIMIT_WAIT_S)
                data = explorer.fetch_stats(token, fen, bands)
            fetched.append((fen, data))
            return explorer._parse(data, fen)

        def worker():
            fetched = []
            try:
                pos = stats if stats is not None else fetch(d.fen, fetched)
                ucis = practical.candidates(board, pos, d.prep_ucis, d.played_uci)
                after_stats = {}
                for uci in ucis:
                    after = board.copy()
                    after.push(chess.Move.from_uci(uci))
                    after_stats[uci] = after_cached.get(uci) or fetch(after.fen(), fetched)
                with chess.engine.SimpleEngine.popen_uci(path) as engine:
                    choices = practical.evaluate(engine, board, ucis, pos, after_stats, d.prep_ucis, d.played_uci)
                self._results.put(("practical", d, (bands, fetched, choices, None)))
            except Exception as e:
                self._results.put(("practical", d, (bands, fetched, None, e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_practical(self, d: my_openings.MyDeviation, bands: str, fetched, choices, error) -> None:
        for fen, data in fetched:                      # the explorer cache, from the main thread
            explorer.save(self.conn, fen, bands, data)
        if choices is not None:
            practical.save(self.conn, bands, d.fen, d.played_uci, choices)
            self._practical[d.key] = choices
        else:
            self._practical[d.key] = f"Couldn't check what works at your level ({error})."
        if d is self._current:
            self._set_text(self._compose(d, self._notes_for.get(d.key)))
            self._show_practical_arrow(d)

    def practical_best(self, d: my_openings.MyDeviation) -> Optional[str]:
        """SAN of the move that works best at your level, if known and neither prep nor yours."""
        got = self._practical.get(d.key)
        if not isinstance(got, list):
            return None
        best = next((c for c in got if c.sound and c.advantage_rate is not None), None)
        return best.san if best is not None and best.uci not in d.prep_ucis and best.uci != d.played_uci else None

    def _show_practical_arrow(self, d: my_openings.MyDeviation) -> None:
        """An amber arrow for the move that works best in practice, if it's neither your prep nor yours."""
        got = self._practical.get(d.key)
        if d is not self._current or not isinstance(got, list):
            return
        best = next((c for c in got if c.sound and c.advantage_rate is not None), None)
        if best is None or best.uci in d.prep_ucis or best.uci == d.played_uci:
            return
        board = self._board(d)
        self.show_position(board, chess.Move.from_uci(d.played_uci), [chess.Move.from_uci(u) for u in d.prep_ucis],
                           f"Move {d.move_number}: you play {d.played_san}",
                           f"Your prep: {'/'.join(d.prep_sans)} (green) · you played {d.played_san} (red) · "
                           f"works best at your level: {best.san} (amber).",
                           extra=[chess.Move.from_uci(best.uci)])

    def _start_batch(self) -> None:
        """Stockfish works out every deviation not looked at yet, one by one in the
        background, so the list can show what each costs (saved, so only once)."""
        todo = [d for d in self._report.deviations if d.key not in self._costs]
        path = engine_locator.find_stockfish()
        if self._batch_running or not todo or path is None:
            return
        self._batch_running = True

        def worker():
            try:
                with chess.engine.SimpleEngine.popen_uci(path) as engine:
                    for d in todo:
                        if self._stop.is_set():
                            break
                        n = opening_commentary.compare(engine, self._board(d), chess.Move.from_uci(d.played_uci),
                                                       [chess.Move.from_uci(u) for u in d.prep_ucis])
                        self._results.put((d, n, None))
            except Exception:
                pass
            self._results.put(("batch done", None, None))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        changed = False
        try:
            while True:
                d, notes, error = self._results.get_nowait()
                if d == "batch done":
                    self._batch_running = False
                    continue
                if d == "practical":
                    self._on_practical(notes, *error)
                    continue
                if notes is not None:
                    opening_commentary.save(self.conn, d.fen, d.played_uci, d.prep_ucis, notes)
                    self._notes_for[d.key] = notes
                    if d.key not in self._costs:
                        self._costs[d.key] = notes.cost
                        changed = True
                if d is self._current:
                    self._set_text(self._compose(d, notes) if notes is not None else
                                   self._compose(d, None) + f"\n\nStockfish couldn't look at it ({error}).")
        except queue.Empty:
            pass
        if changed:
            self._refresh_rows()
        self.after(150, self._poll)

    def _refresh_rows(self) -> None:
        """New costs: re-sort the list, keeping the selection."""
        keep = self._current
        self._on_opening()
        if keep in self._shown:
            i = self._shown.index(keep)
            self.dev_list.selection_set(i)

    # ---------- analysis: your games, or the position on its own ----------

    def _pick_game(self) -> None:
        """The games where you played this deviation; pick one for Game Review at that move."""
        d = self._current
        if d is None or self.review_game is None:
            return
        pick_game_dialog(self, self.conn, d.game_ids, f"{d.line} — with {d.played_san}", d.fen, self.review_game)

    def _analyze(self) -> None:
        """The position on a free analysis board: move either side, Stockfish's evaluation."""
        d = self._current
        if d is None:
            return
        from play_window import PlayWindow
        PlayWindow(self, d.fen, title=f"Analyze — {d.line}", conn=self.conn, analyze=True)

    def _on_tree(self) -> None:
        if self._current is not None:
            self.show_on_tree(self._current)


def pick_game_dialog(parent, conn, game_ids, title: str, fen: Optional[str], review_game: Callable) -> None:
    """A list of games (newest first); double-click or Review opens one in
    Game Review (at `fen`, when the game reaches it)."""
    if not game_ids:
        return
    marks = ",".join("?" * len(game_ids))
    rows = conn.execute(
        f"SELECT id, played_at, opponent_username, result, time_control FROM games WHERE id IN ({marks}) "
        "ORDER BY played_at DESC", list(game_ids)).fetchall()
    win = ctk.CTkToplevel(parent)
    win.title("Your games")
    win.configure(fg_color=theme.BG)
    win.transient(parent.winfo_toplevel())
    win.geometry("440x380")
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=14)
    theme.label(body, f"{title} — {len(rows)} game{'s' if len(rows) != 1 else ''}", "heading",
                wraplength=400).pack(anchor="w")
    theme.label(body, "Double-click (or Review) to open it in Game Review: evaluation graph, "
                      "your mistakes, Stockfish's better moves, Analyze.", "muted", wraplength=400).pack(
        anchor="w", pady=(2, 8))
    box = theme.listbox(body, height=10)
    box.pack(fill="both", expand=True)
    marks_ = {"win": "W", "loss": "L", "draw": "D"}
    for r in rows:
        box.insert(tk.END, f"  {r['played_at'][:10]}  {marks_.get(r['result'], '?')}  vs {r['opponent_username']}"
                           f"  ({r['time_control']})")
    if rows:
        box.selection_set(0)

    def open_selected(_=None):
        sel = box.curselection()
        if sel:
            win.destroy()
            review_game(rows[sel[0]]["id"], fen)

    box.bind("<Double-Button-1>", open_selected)
    box.bind("<Return>", open_selected)
    buttons = ctk.CTkFrame(body, fg_color="transparent")
    buttons.pack(fill="x", pady=(10, 0))
    theme.button(buttons, "Review", open_selected, "primary", height=32, width=100).pack(side="left")
    theme.button(buttons, "Close", win.destroy, height=32, width=80).pack(side="left", padx=(8, 0))
    win.after(50, lambda: (win.lift(), box.focus_set()))
