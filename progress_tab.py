"""Progress dashboard: is the training working? Rating over time against
your goal, blunders per game, and puzzle practice per week. Recomputed
each time the page is shown (no engine involved)."""

from __future__ import annotations

import datetime as dt
import re
import tkinter as tk
from collections import OrderedDict

import customtkinter as ctk

import charts
import db
import theme
from models import time_class

_ELO_RE = {"white": re.compile(r'\[WhiteElo "(\d+)"\]'), "black": re.compile(r'\[BlackElo "(\d+)"\]')}
TIME_CLASSES = ["Rapid", "Blitz", "Bullet", "Daily"]
DEFAULT_GOAL = "2000"


class ProgressTab(ctk.CTkFrame):
    def __init__(self, master, conn):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self._build()
        self.bind("<Map>", lambda e: self.after(50, self.refresh))

    def _build(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        top = ctk.CTkFrame(outer, fg_color="transparent")
        top.pack(fill="x")
        theme.label(top, "Progress", "title").pack(side="left")
        self.class_var = tk.StringVar(value=db.get_setting(self.conn, "progress_class") or "Rapid")
        theme.option_menu(top, TIME_CLASSES, self.class_var, lambda _: self.refresh(), width=120).pack(side="right")
        self.goal_var = tk.StringVar(value=db.get_setting(self.conn, "rating_goal") or DEFAULT_GOAL)
        goal_entry = theme.entry(top, self.goal_var, width=70)
        goal_entry.pack(side="right", padx=(0, 16))
        goal_entry.bind("<Return>", lambda e: self.refresh())
        goal_entry.bind("<FocusOut>", lambda e: self.refresh())
        theme.label(top, "Goal", "muted").pack(side="right", padx=(0, 8))

        self.tiles = ctk.CTkFrame(outer, fg_color="transparent")
        self.tiles.pack(fill="x", pady=(14, 0))
        self.tile_labels = []
        for i in range(5):
            self.tiles.columnconfigure(i, weight=1, uniform="tile")
            tile = theme.card(self.tiles, fg_color=theme.PANEL_ALT)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 4 else 6))
            value = theme.label(tile, "–", "stat")
            value.pack(anchor="w", padx=14, pady=(10, 0))
            caption = theme.label(tile, "", "muted")
            caption.pack(anchor="w", padx=14, pady=(0, 10))
            self.tile_labels.append((value, caption))

        rating_card = theme.card(outer)
        rating_card.pack(fill="both", expand=True, pady=(14, 0))
        theme.label(rating_card, "Rating over time (weekly)", "heading").pack(anchor="w", padx=16, pady=(12, 4))
        self.rating_chart = tk.Canvas(rating_card, height=200, width=600, bg=theme.PANEL_BG, highlightthickness=0)
        self.rating_chart.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        bottom = ctk.CTkFrame(outer, fg_color="transparent")
        bottom.pack(fill="both", expand=True, pady=(14, 0))
        bottom.columnconfigure((0, 1), weight=1, uniform="col")
        bottom.rowconfigure(0, weight=1)
        self.blunder_chart = self._chart_card(bottom, 0, "Blunders per game (weekly avg, analyzed games)")
        self.puzzle_chart = self._chart_card(bottom, 1, "Puzzle practice per week")

    def _chart_card(self, parent, col: int, title: str) -> tk.Canvas:
        card = theme.card(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0, 7) if col == 0 else (7, 0))
        theme.label(card, title, "heading").pack(anchor="w", padx=16, pady=(12, 4))
        canvas = tk.Canvas(card, height=150, width=300, bg=theme.PANEL_BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        return canvas

    # ---------- data ----------

    def _ratings(self, tclass: str):
        """[(date, rating)] of your rating in each game of this time class."""
        out = []
        for g in self.conn.execute("SELECT pgn, my_color, time_control, played_at FROM games ORDER BY played_at"):
            if time_class(g["time_control"]) != tclass:
                continue
            m = _ELO_RE[g["my_color"]].search(g["pgn"])
            if m:
                out.append((dt.datetime.fromisoformat(g["played_at"]).date(), int(m.group(1))))
        return out

    @staticmethod
    def _week(day: dt.date) -> dt.date:
        return day - dt.timedelta(days=day.weekday())

    def refresh(self) -> None:
        tclass = self.class_var.get()
        db.set_setting(self.conn, "progress_class", tclass)
        try:
            goal = int(self.goal_var.get())
            db.set_setting(self.conn, "rating_goal", str(goal))
        except ValueError:
            goal = None

        ratings = self._ratings(tclass)
        weekly = OrderedDict()
        for day, rating in ratings:
            weekly[self._week(day)] = rating          # last rating of each week
        weekly = list(weekly.items())[-40:]           # last ~9 months keeps the chart readable
        charts.line_chart(self.rating_chart, [(d.strftime("%b %d"), r) for d, r in weekly], goal=goal,
                          empty_text=f"No {tclass.lower()} games yet")

        # tiles
        current = ratings[-1][1] if ratings else None
        month_ago = next((r for d, r in reversed(ratings) if d <= dt.date.today() - dt.timedelta(days=30)), None)
        peak = max((r for _, r in ratings), default=None)
        week_start = self._week(dt.date.today()).isoformat()
        puzzle_weeks = db.attempts_by_week(self.conn, "puzzle")
        this_week = next(((a, c) for w, a, c in puzzle_weeks if w == week_start), (0, 0))
        weak = len(db.weak_item_ids(self.conn, "puzzle"))
        change = (current - month_ago) if current is not None and month_ago is not None else None
        tiles = [
            (str(current) if current else "–", f"current {tclass.lower()} rating"),
            (f"{change:+d}" if change is not None else "–", "last 30 days"),
            (f"{goal - current}" if goal and current else "–", f"to go to {goal}" if goal else "set a goal"),
            (f"{this_week[1]}/{this_week[0]}", "puzzles solved this week"),
            (str(weak), "weak spots to fix"),
        ]
        for (value, caption), (vl, cl) in zip(tiles, self.tile_labels):
            color = theme.TEXT
            if value.startswith("+"):
                color = theme.SUCCESS
            elif value.startswith("-"):
                color = theme.ERROR
            vl.configure(text=value, text_color=color)
            cl.configure(text=caption)
        if peak and current:
            self.tile_labels[0][1].configure(text=f"current {tclass.lower()} rating (peak {peak})")

        # blunders per analyzed game, weekly average
        per_game = {r["game_id"]: (r["played_at"], r["n"]) for r in self.conn.execute(
            """SELECT e.game_id, g.played_at, g.time_control,
                      SUM(e.cp_after IS NOT NULL AND e.cp_before - e.cp_after >= 300) AS n
               FROM move_evaluations e JOIN games g ON g.id = e.game_id
               GROUP BY e.game_id""") if time_class(r["time_control"]) == tclass}
        blunder_weeks = OrderedDict()
        for played_at, n in sorted(per_game.values()):
            week = self._week(dt.datetime.fromisoformat(played_at).date())
            blunder_weeks.setdefault(week, []).append(n)
        points = [(w.strftime("%b %d"), sum(v) / len(v)) for w, v in list(blunder_weeks.items())[-30:]]
        charts.line_chart(self.blunder_chart, points, color=theme.ERROR, value_fmt="{:.1f}",
                          empty_text="Analyze more games to see this")

        charts.bar_chart(self.puzzle_chart,
                         [(dt.date.fromisoformat(w).strftime("%b %d"), a, c) for w, a, c in puzzle_weeks[-12:]],
                         labels=("tried", "solved"), empty_text="Solve some puzzles to see this")
