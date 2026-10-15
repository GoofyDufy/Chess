"""Progress → Themes and Progress → This week (data in theme_stats.py and
weekly.py). Buttons jump to where you practise: Puzzle Review on a theme or a
puzzle, Game Review, or the Tactics & Threats page.

`actions` (from gui.py): practice_theme(tag), open_puzzle(id), review_game(game_id,
fen), threats(), tactics(), analyze() — any may be missing."""

from __future__ import annotations

import datetime as dt
import queue
import threading
from typing import Callable, Dict, Optional

import customtkinter as ctk

import db
import explorer
import lichess_puzzles
import theme
import theme_stats
import weekly

LICHESS_BATCH = 100       # "+ Lichess": puzzles of one theme added at a time


def _scroll(parent) -> ctk.CTkScrollableFrame:
    column = ctk.CTkScrollableFrame(parent, fg_color="transparent", scrollbar_button_color=theme.CONTROL_BG,
                                    scrollbar_button_hover_color=theme.BORDER_STRONG)
    column.columnconfigure(0, weight=1)
    return column


def _section(parent, title: str, help_text: str = "") -> ctk.CTkFrame:
    card = theme.card(parent)
    card.pack(fill="x", pady=(0, 14))
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=16, pady=14)
    theme.fit_wrap(inner)
    theme.label(inner, title, "heading").pack(anchor="w")
    if help_text:
        theme.label(inner, help_text, "muted", wraplength=600).pack(anchor="w", fill="x", pady=(2, 8))
    return inner


def _rate_color(rate: float) -> str:
    return theme.SUCCESS if rate >= 0.7 else theme.WARNING if rate >= 0.45 else theme.ERROR


def _small_button(parent, text: str, command, primary: bool = False) -> ctk.CTkButton:
    return theme.button(parent, text, command, "primary" if primary else "secondary", height=28,
                        width=max(64, 9 * len(text) + 20), font=theme.FONT_SMALL)


class ThemesView(ctk.CTkFrame):
    def __init__(self, master, conn, actions: Dict[str, Callable]):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.actions = actions
        self._results: "queue.Queue" = queue.Queue()
        self._busy: Optional[str] = None             # theme being downloaded
        self.body = _scroll(self)
        self.body.pack(fill="both", expand=True)
        self.after(150, self._poll)

    def refresh(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        skills = _section(self.body, "Seeing the board",
                          "From the Tactics & Threats page: do you notice what's going on before you move?")
        for s in theme_stats.skills(self.conn):
            drill = self.actions.get("threats") if "threat" in s.name else self.actions.get("tactics")
            self._row(skills, s.name, s.hits, s.total, None, [("Drill", drill)] if drill else [])

        rows = theme_stats.theme_rows(self.conn, since_recent=_days_ago(30))
        themes = _section(self.body, "Puzzle themes — weakest first",
                          f"Your first tries at puzzles, by tag (rated after {theme_stats.MIN_TRIES} tries). "
                          "Practice: every puzzle with that tag, unsolved first. + Lichess: add "
                          f"{LICHESS_BATCH} Lichess puzzles of that theme at your rating.")
        if not rows:
            theme.label(themes, "Solve some puzzles in Puzzle Review to see your themes here.", "body").pack(anchor="w")
        for r in rows:
            buttons = []
            if self.actions.get("practice_theme"):
                buttons.append(("Practice", lambda t=r.tag: self.actions["practice_theme"](t)))
            if r.lichess_theme:
                buttons.append(("+ Lichess", lambda r=r: self._add_lichess(r)))
            recent = (f"30 days: {r.recent_solved}/{r.recent_tries}" if r.recent_tries else "")
            self._row(themes, r.tag + ("  (your tag)" if r.yours else ""), r.solved, r.tries, recent, buttons,
                      rated=r.tries >= theme_stats.MIN_TRIES, key=r.tag)

    def _row(self, parent, name: str, hits: int, total: int, note: Optional[str], buttons,
             rated: bool = True, key: Optional[str] = None) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        # fixed columns: name · bar · rate · note (fills) · buttons (fixed slots, right-aligned)
        slots = ctk.CTkFrame(row, fg_color="transparent", width=180, height=28)
        slots.pack(side="right")
        slots.pack_propagate(False)
        for label, command in reversed(buttons):
            _small_button(slots, label, command, primary=label == "Practice").pack(side="right", padx=(6, 0))
        theme.label(row, name, "body", width=230).pack(side="left")
        rate = hits / total if total else 0
        bar = ctk.CTkProgressBar(row, width=160, height=8, corner_radius=4, fg_color=theme.CONTROL_BG,
                                 progress_color=_rate_color(rate) if rated and total else theme.CONTROL_BG)
        bar.set(rate if total else 0)
        bar.pack(side="left", padx=(0, 12))
        text = f"{rate:.0%}  ({hits}/{total})" if total else "not tried yet"
        if total and not rated:
            text += " · too few"
        theme.label(row, text, "body", width=130).pack(side="left")
        status = theme.label(row, note or "", "muted")
        status.pack(side="left", padx=(8, 0))
        if key:
            row._status = status
            row._key = key

    # ---------- "+ Lichess": puzzles of one theme ----------

    def _add_lichess(self, r) -> None:
        if self._busy:
            return
        self._busy = r.tag
        low, high = lichess_puzzles.default_range(explorer.my_recent_rating(self.conn))
        low = int(db.get_setting(self.conn, "lichess_min_rating") or low)
        high = int(db.get_setting(self.conn, "lichess_max_rating") or high)
        skip = lichess_puzzles.ids(self.conn)
        theme_id = r.lichess_theme
        self._set_status(r.tag, f"Adding Lichess {r.tag.lower()} puzzles…")
        stop = threading.Event()

        def worker():
            found, error = 0, None
            try:
                found = lichess_puzzles.download(
                    low, high, LICHESS_BATCH, skip, lambda b: self._results.put(("batch", b)), stop,
                    lambda f, s: self._results.put(("progress", r.tag, f, s)), theme=theme_id)
            except Exception as e:
                error = e
            self._results.put(("done", r.tag, found, error))

        threading.Thread(target=worker, daemon=True).start()

    def _set_status(self, tag: str, text: str) -> None:
        for section in self.body.winfo_children():
            for inner in section.winfo_children():
                for row in inner.winfo_children():
                    if getattr(row, "_key", None) == tag:
                        row._status.configure(text=text)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._results.get_nowait()
                if item[0] == "batch":
                    lichess_puzzles.save(self.conn, item[1])
                elif item[0] == "progress":
                    _, tag, found, scanned = item
                    self._set_status(tag, f"{found} found · {scanned:,} Lichess puzzles checked…")
                else:
                    _, tag, found, error = item
                    self._busy = None
                    self._set_status(tag, f"Couldn't reach Lichess ({type(error).__name__})." if error else
                                     f"Added {found} — press Practice.")
        except queue.Empty:
            pass
        self.after(150, self._poll)


class WeekView(ctk.CTkFrame):
    def __init__(self, master, conn, actions: Dict[str, Callable]):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.actions = actions
        self.body = _scroll(self)
        self.body.pack(fill="both", expand=True)

    def refresh(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        w = weekly.review(self.conn)
        end = w.start + dt.timedelta(days=weekly.DAYS - 1)
        head = _section(self.body, f"Your week: {w.start.strftime('%b %d')} – {end.strftime('%b %d')}")
        tiles = ctk.CTkFrame(head, fg_color="transparent")
        tiles.pack(fill="x", pady=(8, 0))
        rating = "–"
        if w.rating_from is not None:
            change = w.rating_to - w.rating_from
            rating = f"{w.rating_to} ({change:+d})"
        practice = (f"puzzles solved · plus {w.spot_rounds} tactic, {w.threat_rounds} threat rounds"
                    + (f", {w.lines_drilled} lines" if w.lines_drilled else ""))
        for i, (value, caption) in enumerate((
                (str(w.games), f"games · {w.wins} won, {w.losses} lost, {w.draws} drawn"),
                (rating, f"{w.rating_class.lower()} rating" if w.rating_class else "rating"),
                (str(w.mistake_count), f"mistakes in {w.analyzed} analyzed game(s)"),
                (f"{w.puzzles_solved}/{w.puzzles_tried}", practice))):
            tiles.columnconfigure(i, weight=1, uniform="t")
            tile = theme.card(tiles, fg_color=theme.PANEL_ALT)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            box = ctk.CTkFrame(tile, fg_color="transparent")
            box.pack(fill="x", padx=12, pady=8)
            theme.fit_wrap(box)
            theme.label(box, value, "stat").pack(anchor="w")
            theme.label(box, caption, "muted", wraplength=140).pack(anchor="w", fill="x")
        if not w.games:
            theme.label(head, "No games in the last 7 days — import your latest games on Import & Analyze.",
                        "body").pack(anchor="w", pady=(10, 0))

        if w.mistakes:
            sec = _section(self.body, "Your costliest mistakes",
                           "The moves that threw away the most of your winning chances (going from winning "
                           "to losing counts most; a blunder when already lost hardly counts). "
                           "Puzzle: try the position again. Review: see it in the game.")
            for m in w.mistakes:
                row = ctk.CTkFrame(sec, fg_color="transparent")
                row.pack(fill="x", pady=4)
                # buttons first, so a long line can never squeeze them
                if self.actions.get("review_game"):
                    _small_button(row, "Review", lambda m=m: self.actions["review_game"](m.game_id, m.fen)).pack(
                        side="right", padx=(6, 0))
                if self.actions.get("open_puzzle"):
                    _small_button(row, "Puzzle", lambda m=m: self.actions["open_puzzle"](m.puzzle_id), True).pack(
                        side="right")
                text = ctk.CTkFrame(row, fg_color="transparent")
                text.pack(side="left", fill="x", expand=True)
                theme.label(text, f"vs {m.opponent}, move {m.move_number}: you played {m.played_san}, "
                                  f"best was {m.best_san} — {m.swing_text()}", "body").pack(anchor="w")
                tags = " · ".join(t for t in (m.kind, m.puzzle_type) if t)
                theme.label(text, f"threw away {m.cost:.0%} of your winning chances"
                                  + (f" · {tags}" if tags else ""), "muted").pack(anchor="w")

        if w.mistake_count:
            sec = _section(self.body, "The pattern")
            lines = [f"{w.facing_threat} of your {w.mistake_count} mistakes ({w.facing_threat / w.mistake_count:.0%}) "
                     "came when your opponent had a threat (Defensive / Counterattack puzzles)."]
            if w.common_type:
                lines.append(f"Most common: {w.common_type} ({w.common_type_count}).")
            if w.endgame:
                lines.append(f"{w.endgame} in the endgame.")
            theme.label(sec, "\n".join(lines), "body", wraplength=600).pack(anchor="w", fill="x")

        sec = _section(self.body, "What to practise next")
        for s in w.suggestions:
            row = ctk.CTkFrame(sec, fg_color="transparent")
            row.pack(fill="x", pady=4)
            theme.label(row, s.text, "body", wraplength=560).pack(side="left")
            label, command = {
                "threats": ("Drill threats", self.actions.get("threats")),
                "tactics": ("Spot the Tactic", self.actions.get("tactics")),
                "analyze": ("Import & Analyze", self.actions.get("analyze")),
                "theme": ("Practice", (lambda t=s.arg: self.actions["practice_theme"](t))
                          if self.actions.get("practice_theme") else None),
            }[s.action]
            if command:
                _small_button(row, label, command, True).pack(side="right")


def _days_ago(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()
