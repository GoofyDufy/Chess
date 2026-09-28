"""Progress dashboard: is the training working? Rating over time against
your goal, blunders per game, and puzzle practice per week. Recomputed
each time the page is shown (no engine involved)."""

from __future__ import annotations

import datetime as dt
import tkinter as tk

import customtkinter as ctk

import charts
import db
import progress_data
import theme

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

    def refresh(self) -> None:
        tclass = self.class_var.get()
        db.set_setting(self.conn, "progress_class", tclass)
        try:
            goal = int(self.goal_var.get())
            db.set_setting(self.conn, "rating_goal", str(goal))
        except ValueError:
            goal = None
        data = progress_data.compute(self.conn, tclass, goal)

        def label(iso: str) -> str:
            return dt.date.fromisoformat(iso).strftime("%b %d")

        charts.line_chart(self.rating_chart, [(label(d), r) for d, r in data["weekly_ratings"]], goal=goal,
                          empty_text=f"No {tclass.lower()} games yet")
        current, change = data["current_rating"], data["change_30_days"]
        week = data["puzzles_this_week"]
        tiles = [
            (str(current) if current else "–",
             f"current {tclass.lower()} rating" + (f" (peak {data['peak_rating']})" if current else "")),
            (f"{change:+d}" if change is not None else "–", "last 30 days"),
            (str(data["to_goal"]) if data["to_goal"] is not None else "–",
             f"to go to {goal}" if goal else "set a goal"),
            (f"{week['solved']}/{week['tried']}", "puzzles solved this week"),
            (str(data["weak_spots"]), "weak spots to fix"),
        ]
        for (value, caption), (vl, cl) in zip(tiles, self.tile_labels):
            color = theme.SUCCESS if value.startswith("+") else theme.ERROR if value.startswith("-") else theme.TEXT
            vl.configure(text=value, text_color=color)
            cl.configure(text=caption)

        charts.line_chart(self.blunder_chart, [(label(w), v) for w, v in data["blunders_per_game_weekly"]],
                          color=theme.ERROR, value_fmt="{:.1f}", empty_text="Analyze more games to see this")
        charts.bar_chart(self.puzzle_chart, [(label(w), a, c) for w, a, c in data["puzzle_weeks"]],
                         labels=("tried", "solved"), empty_text="Solve some puzzles to see this")
