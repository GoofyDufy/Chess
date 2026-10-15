"""How well you do per theme (Progress → Themes): every first try at a
puzzle, grouped by the puzzle's tags — its kind, type, phase, Lichess themes
and your own tags (puzzle_tags.py). Plus the two "seeing" skills from the
Tactics & Threats page. No engine involved."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List

import db
import lichess_puzzles
import puzzle_tags
import spot
import stats
import threat

MIN_TRIES = 3            # a theme needs this many tries before it's rated
# tags that say how long / how big / whose game — not a theme to practise
_NOT_THEMES = {"one move", "2 moves", "3 moves", "4+ moves", "4 moves", "5 moves", "sharp", "quiet",
               "master game", "master vs master", "super gm game", "crushing", "advantage", "equality",
               "blunder", "mistake", "inaccuracy", "long", "short", "very long"}


@dataclass
class ThemeRow:
    tag: str
    tries: int = 0
    solved: int = 0                 # right on the first try
    near: int = 0                   # near-best
    recent_tries: int = 0           # the last 30 days
    recent_solved: int = 0
    yours: bool = False             # one of your own tags

    @property
    def rate(self) -> float:
        return self.solved / self.tries if self.tries else 0.0

    @property
    def lichess_theme(self) -> str:
        return puzzle_tags.lichess_theme(self.tag)


@dataclass
class Skill:
    name: str
    hits: int
    total: int

    @property
    def rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


def _puzzles(conn: sqlite3.Connection) -> Dict[tuple, object]:
    """(item_type, id) -> Puzzle, for both kinds of puzzle."""
    out = {}
    for r in conn.execute("SELECT * FROM puzzles"):
        out[("puzzle", r["id"])] = db._row_to_puzzle(r)
    for r in conn.execute("SELECT * FROM lichess_puzzles"):
        out[(lichess_puzzles.ITEM_TYPE, r["id"])] = lichess_puzzles.to_puzzle(r)
    return out


def theme_rows(conn: sqlite3.Connection, since_recent: str = "") -> List[ThemeRow]:
    """Every theme with at least one try, weakest first among the rated ones."""
    puzzles = _puzzles(conn)
    user = {}
    for tag, keys in puzzle_tags.all_user_tags(conn).items():
        for key in keys:
            user.setdefault(key, set()).add(tag)
    tags_of: Dict[tuple, List[tuple]] = {}
    rows: Dict[str, ThemeRow] = {}
    for r in conn.execute("SELECT item_type, item_id, outcome, correct, attempted_at FROM attempts "
                          "WHERE item_type IN ('puzzle', ?)", (lichess_puzzles.ITEM_TYPE,)):
        key = (r["item_type"], r["item_id"])
        p = puzzles.get(key)
        if p is None:                     # rebuilt at another blunder size since: its tags are gone
            continue
        if key not in tags_of:
            auto = [(t, False) for t in puzzle_tags.auto_tags(p, False, stats.is_endgame(p.fen))
                    if t.lower() not in _NOT_THEMES]
            have = {t.lower() for t, _ in auto}
            mine = [(t, True) for t in user.get(puzzle_tags.key(p), ()) if t.lower() not in have]
            tags_of[key] = auto + mine
        outcome = db.attempt_outcome(r)
        recent = r["attempted_at"] >= since_recent if since_recent else False
        for tag, yours in tags_of[key]:
            row = rows.setdefault(tag.lower(), ThemeRow(tag, yours=yours))
            row.tries += 1
            row.solved += outcome == "correct"
            row.near += outcome == "near_best"
            row.recent_tries += recent
            row.recent_solved += recent and outcome == "correct"
    rated = sorted((r for r in rows.values() if r.tries >= MIN_TRIES), key=lambda r: (r.rate, -r.tries))
    unrated = sorted((r for r in rows.values() if r.tries < MIN_TRIES), key=lambda r: -r.tries)
    return rated + unrated


def skills(conn: sqlite3.Connection) -> List[Skill]:
    t, s = threat.summary(conn), spot.summary(conn)
    return [
        Skill("Seeing your opponent's threats", t.found, t.threats),
        Skill("Answering threats well", t.answers_ok, t.rounds),
        Skill("Spotting tactics", s.spotted, s.tactics),
        Skill("Not crying wolf (quiet positions)", s.quiet - s.false_alarms, s.quiet),
    ]
