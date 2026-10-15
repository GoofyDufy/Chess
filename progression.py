"""XP and levels. Nothing is stored: XP is recomputed from the attempts
table (every puzzle / opening-line first try), so practice done before
this existed counts too, and changing a rule below re-scores everything.

Tabs call notify() after recording an attempt; the sidebar's level card
listens and shows the XP gained."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import db
import sac
import spot
import threat

# ---- XP per first try ----
PUZZLE_XP = {"correct": 10, "near_best": 5, "missed": 2, "revealed": 0}
SHARP_BONUS_XP = 5        # solved a puzzle with only one clearly best move
LINE_CLEAN_XP = 15        # drilled a whole line without a miss
LINE_MISSED_XP = 5
WEAK_SPOT_CLEARED_XP = 25  # solved twice in a row after missing it (leaves Weak spots)
# only the first try at each puzzle / line per day earns XP, so re-solving
# the same easy puzzle can't farm levels (weak-spot bonuses still count)

# ---- levels ----
PIECES = ("Pawn", "Knight", "Bishop", "Rook", "Queen", "King")
STEPS = ("I", "II", "III")
LEVEL_NAMES = [f"{piece} {step}" for piece in PIECES for step in STEPS]
FIRST_LEVEL_COSTS = (10, 25, 100)   # XP to finish Pawn I, Pawn II, Pawn III
LEVEL_GROWTH = 1.3                  # after that, each level costs 30% more than the last


def _level_costs() -> List[int]:
    costs = list(FIRST_LEVEL_COSTS)
    while len(costs) < len(LEVEL_NAMES) - 1:   # the top level has no cost — it's the end
        costs.append(int(round(costs[-1] * LEVEL_GROWTH / 5)) * 5)
    return costs


LEVEL_COSTS = _level_costs()


@dataclass
class Level:
    number: int                # 1-based
    name: str                  # e.g. "Knight II"
    total_xp: int
    xp_into_level: int
    xp_for_level: Optional[int]  # None at the top level

    @property
    def progress(self) -> float:
        return 1.0 if not self.xp_for_level else self.xp_into_level / self.xp_for_level


def level_for(total_xp: int) -> Level:
    remaining = total_xp
    for i, cost in enumerate(LEVEL_COSTS):
        if remaining < cost:
            return Level(i + 1, LEVEL_NAMES[i], total_xp, remaining, cost)
        remaining -= cost
    return Level(len(LEVEL_NAMES), LEVEL_NAMES[-1], total_xp, remaining, None)


def total_xp(conn: sqlite3.Connection) -> int:
    return _attempts_xp(conn) + spot.xp(conn) + threat.xp(conn) + sac.xp(conn)    # + Spot the Tactic / threat rounds (own tables)


def _attempts_xp(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT item_type, item_id, correct, outcome, sharp, attempted_at FROM attempts ORDER BY attempted_at"
    ).fetchall()
    xp = 0
    scored_today: set = set()                 # (type, id, date) already paid out
    history: Dict[Tuple[str, str], List[int]] = {}
    weak: set = set()                         # items currently in Weak spots
    for r in rows:
        key = (r["item_type"], r["item_id"])
        day_key = key + (r["attempted_at"][:10],)
        outcome = db.attempt_outcome(r)
        if day_key not in scored_today:
            scored_today.add(day_key)
            if r["item_type"] in ("puzzle", "lichess"):          # your games' / Lichess puzzles
                xp += PUZZLE_XP.get(outcome, 0)
                if outcome == "correct" and r["sharp"]:
                    xp += SHARP_BONUS_XP
            else:
                xp += LINE_CLEAN_XP if r["correct"] else LINE_MISSED_XP
        # same rule as db.weak_item_ids: any miss makes it weak until two right in a row
        results = history.setdefault(key, [])
        results.append(r["correct"])
        if not r["correct"]:
            weak.add(key)
        elif key in weak and results[-2:] == [1, 1]:
            weak.discard(key)
            xp += WEAK_SPOT_CLEARED_XP
    return xp


# ---- change notifications ----
_listeners: List[Callable[[], None]] = []


def add_listener(callback: Callable[[], None]) -> None:
    _listeners.append(callback)


def clear_listeners() -> None:
    _listeners.clear()


def notify() -> None:
    """Call after recording or updating an attempt."""
    for callback in list(_listeners):
        callback()
