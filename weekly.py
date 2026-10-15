"""Your week in review (Progress → This week): the games of the last 7 days,
their costliest mistakes, the pattern behind them, what you practised, and
what to practise next. Plain data, no engine involved.

"Costliest" is measured the way Lichess judges moves: by how much of your
chance of winning the move threw away (spot.win_chances), not by pawns — so
going from +4 to -5 counts far more than allowing mate when already -8."""

from __future__ import annotations

import collections
import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

import chess

import db
import lichess_puzzles
import spot
import progress_data
import stats
import theme_stats
import threat
from analyzer import MATE_CP
from models import time_class

DAYS = 7
TOP_MISTAKES = 3
FACING_THREAT = ("Defensive", "Counterattack")


@dataclass
class Mistake:
    puzzle_id: str
    game_id: str
    fen: str
    opponent: str
    played_at: dt.datetime
    move_number: int
    played_san: str
    best_san: str
    before_cp: int               # with the best move, your point of view
    after_cp: int                # after what you played
    kind: Optional[str]
    puzzle_type: Optional[str]

    @property
    def cost(self) -> float:
        """Share of the game's result thrown away: 0 .. 1 (winning -> lost)."""
        return max(0.0, (spot.win_chances(self.before_cp) - spot.win_chances(self.after_cp)) / 2)

    def swing_text(self) -> str:
        """'+4.1 → −5.7, winning → losing'."""
        return f"{_eval(self.before_cp)} → {_eval(self.after_cp)}, {_state(self.before_cp)} → {_state(self.after_cp)}"


def _eval(cp: int) -> str:
    if abs(cp) >= MATE_CP // 2:
        return "mating" if cp > 0 else "mated"
    return f"{cp / 100:+.1f}".replace("-", "−")


def _state(cp: int) -> str:
    """Lichess's bands: +2 or more is a decisive advantage."""
    return "winning" if cp >= 200 else "losing" if cp <= -200 else "about equal"


@dataclass
class Suggestion:
    text: str
    action: str                  # "threats" / "theme" / "analyze" / "tactics"
    arg: str = ""


@dataclass
class Week:
    start: dt.date
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    analyzed: int = 0
    rating_class: Optional[str] = None
    rating_from: Optional[int] = None
    rating_to: Optional[int] = None
    mistakes: List[Mistake] = field(default_factory=list)
    mistake_count: int = 0
    facing_threat: int = 0        # of this week's mistakes, made when facing a threat
    endgame: int = 0
    common_type: Optional[str] = None
    common_type_count: int = 0
    puzzles_tried: int = 0
    puzzles_solved: int = 0
    spot_rounds: int = 0
    threat_rounds: int = 0
    lines_drilled: int = 0
    suggestions: List[Suggestion] = field(default_factory=list)


def review(conn: sqlite3.Connection, today: Optional[dt.date] = None) -> Week:
    today = today or dt.date.today()
    start = today - dt.timedelta(days=DAYS - 1)
    since = start.isoformat()
    w = Week(start)

    games = conn.execute("SELECT id, result, time_control, is_analyzed FROM games WHERE played_at >= ?",
                         (since,)).fetchall()
    w.games = len(games)
    for g in games:
        w.wins += g["result"] == "win"
        w.losses += g["result"] == "loss"
        w.draws += g["result"] == "draw"
    w.analyzed = conn.execute(
        "SELECT COUNT(DISTINCT e.game_id) FROM move_evaluations e JOIN games g ON g.id = e.game_id "
        "WHERE g.played_at >= ?", (since,)).fetchone()[0]

    # rating: the time class you played most this week
    classes = collections.Counter(time_class(g["time_control"]) for g in games)
    if classes:
        w.rating_class = classes.most_common(1)[0][0]
        rated = progress_data.ratings(conn, w.rating_class)
        before = [r for d, r in rated if d < start]
        during = [r for d, r in rated if d >= start]
        if during:
            w.rating_from = before[-1] if before else during[0]
            w.rating_to = during[-1]

    # mistakes = this week's puzzles (your mistakes of at least the blunder size)
    rows = conn.execute(
        """SELECT p.id, p.fen, p.played_move_san, p.correct_move_san, p.eval_before_cp, p.eval_after_cp, p.kind,
                  p.puzzle_type, g.id AS game_id, g.opponent_username, g.played_at, e.ply
           FROM puzzles p JOIN games g ON g.id = p.source_game_id
           LEFT JOIN move_evaluations e ON e.game_id = g.id AND e.fen_before = p.fen
           WHERE g.played_at >= ?""", (since,)).fetchall()
    w.mistake_count = len(rows)
    w.facing_threat = sum(r["kind"] in FACING_THREAT for r in rows)
    w.endgame = sum(stats.is_endgame(r["fen"]) for r in rows)
    types = collections.Counter(r["puzzle_type"] for r in rows      # a pattern, not how big it was
                                if r["puzzle_type"] and r["puzzle_type"] not in ("Blunder", "Mistake", "Inaccuracy"))
    if types:
        w.common_type, w.common_type_count = types.most_common(1)[0]
    mistakes = []
    for r in rows:
        if r["eval_before_cp"] is None or r["eval_after_cp"] is None:
            continue
        ply = r["ply"] if r["ply"] is not None else 2 * (chess.Board(r["fen"]).fullmove_number - 1)
        mistakes.append(Mistake(
            r["id"], r["game_id"], r["fen"], r["opponent_username"], dt.datetime.fromisoformat(r["played_at"]),
            ply // 2 + 1, r["played_move_san"] or "?", r["correct_move_san"], r["eval_before_cp"],
            r["eval_after_cp"], r["kind"], r["puzzle_type"]))
    w.mistakes = sorted(mistakes, key=lambda m: -m.cost)[:TOP_MISTAKES]

    # practice this week
    for r in conn.execute("SELECT item_type, outcome, correct FROM attempts WHERE attempted_at >= ?", (since,)):
        if r["item_type"] in ("puzzle", lichess_puzzles.ITEM_TYPE):
            w.puzzles_tried += 1
            w.puzzles_solved += db.attempt_outcome(r) == "correct"
        else:
            w.lines_drilled += 1
    w.spot_rounds = conn.execute("SELECT COUNT(*) FROM spot_attempts WHERE attempted_at >= ?", (since,)).fetchone()[0]
    w.threat_rounds = threat.summary(conn, since).rounds

    w.suggestions = _suggest(conn, w)
    return w


def _suggest(conn: sqlite3.Connection, w: Week) -> List[Suggestion]:
    out = []
    if w.games > w.analyzed:
        out.append(Suggestion(f"Analyze this week's {w.games - w.analyzed} new game(s) — your mistakes "
                              "become puzzles.", "analyze"))
    if w.mistake_count and w.facing_threat * 2 >= w.mistake_count:
        out.append(Suggestion(f"{w.facing_threat} of your {w.mistake_count} mistakes came when your opponent "
                              "had a threat. Drill: What's the threat?", "threats"))
    weak = next((r for r in theme_stats.theme_rows(conn) if r.tries >= theme_stats.MIN_TRIES and r.rate < 0.6), None)
    if weak:
        out.append(Suggestion(f"Your weakest theme: {weak.tag} ({weak.rate:.0%} solved). Practise it.",
                              "theme", weak.tag))
    if not out or w.spot_rounds + w.threat_rounds == 0:
        out.append(Suggestion("A few rounds of Spot the Tactic keep your eyes sharp.", "tactics"))
    return out
