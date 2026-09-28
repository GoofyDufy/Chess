"""Progress numbers (rating vs goal, blunders per game, puzzle practice)
as plain data — shared by the desktop Progress page and the server API."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from collections import OrderedDict
from typing import Dict, Optional

import db
from models import time_class

_ELO_RE = {"white": re.compile(r'\[WhiteElo "(\d+)"\]'), "black": re.compile(r'\[BlackElo "(\d+)"\]')}


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def ratings(conn: sqlite3.Connection, tclass: str):
    """[(date, rating)] of your rating in each game of this time class, oldest first."""
    out = []
    for g in conn.execute("SELECT pgn, my_color, time_control, played_at FROM games ORDER BY played_at"):
        if time_class(g["time_control"]) != tclass:
            continue
        m = _ELO_RE[g["my_color"]].search(g["pgn"])
        if m:
            out.append((dt.datetime.fromisoformat(g["played_at"]).date(), int(m.group(1))))
    return out


def compute(conn: sqlite3.Connection, tclass: str = "Rapid", goal: Optional[int] = None) -> Dict:
    rated = ratings(conn, tclass)
    weekly = OrderedDict()
    for day, rating in rated:
        weekly[week_start(day)] = rating                 # last rating of each week
    current = rated[-1][1] if rated else None
    month_ago = next((r for d, r in reversed(rated) if d <= dt.date.today() - dt.timedelta(days=30)), None)

    per_game = [(r["played_at"], r["n"]) for r in conn.execute(
        """SELECT g.played_at, g.time_control,
                  SUM(e.cp_after IS NOT NULL AND e.cp_before - e.cp_after >= 300) AS n
           FROM move_evaluations e JOIN games g ON g.id = e.game_id
           GROUP BY e.game_id""") if time_class(r["time_control"]) == tclass]
    blunder_weeks = OrderedDict()
    for played_at, n in sorted(per_game):
        blunder_weeks.setdefault(week_start(dt.datetime.fromisoformat(played_at).date()), []).append(n)

    puzzle_weeks = db.attempts_by_week(conn, "puzzle")
    this_week = week_start(dt.date.today()).isoformat()
    tried, solved = next(((a, c) for w, a, c in puzzle_weeks if w == this_week), (0, 0))
    return {
        "time_class": tclass,
        "goal": goal,
        "current_rating": current,
        "peak_rating": max((r for _, r in rated), default=None),
        "change_30_days": (current - month_ago) if current is not None and month_ago is not None else None,
        "to_goal": (goal - current) if goal and current else None,
        "weekly_ratings": [(d.isoformat(), r) for d, r in list(weekly.items())[-40:]],
        "blunders_per_game_weekly": [(w.isoformat(), sum(v) / len(v)) for w, v in list(blunder_weeks.items())[-30:]],
        "puzzle_weeks": puzzle_weeks[-12:],
        "puzzles_this_week": {"tried": tried, "solved": solved},
        "weak_spots": len(db.weak_item_ids(conn, "puzzle")),
    }
