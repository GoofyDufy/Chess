"""Tags on a puzzle: the ones it gets automatically (its kind, type, length,
sharp / quiet, Lichess's themes) and the ones you add yourself.

Your tags are saved by position + best move for your games' puzzles (their
ids change whenever puzzles are rebuilt at a new blunder size) and by id for
Lichess puzzles, so they're never lost. Names follow Lichess's theme names
(lichess.org/training/themes) wherever there is one.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Dict, List, Set

from models import Puzzle, PuzzleSource

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_tags (
    puzzle_key TEXT,              -- see key()
    tag TEXT,
    added_at TEXT,
    PRIMARY KEY (puzzle_key, tag)
);
"""

# offered under "+ Tag" (anything else can be typed in)
SUGGESTED = (
    "Attacking", "Defensive", "Counterattack", "Positional",
    "Fork", "Pin", "Skewer", "Discovered attack", "Double check", "Hanging piece", "Trapped piece",
    "Sacrifice", "Deflection", "Attraction", "Interference", "Clearance", "Intermezzo", "X-ray attack",
    "Quiet move", "Defensive move", "Zugzwang", "Promotion", "Exposed king", "Back rank mate", "Mate",
    "Endgame",
)

# Lichess theme ids that don't read well split at capitals
_THEME_NAMES = {
    "mateIn1": "Mate in 1", "mateIn2": "Mate in 2", "mateIn3": "Mate in 3", "mateIn4": "Mate in 4",
    "mateIn5": "Mate in 5+", "xRayAttack": "X-ray attack", "attackingF2F7": "Attacking f2/f7",
    "oneMove": "One move", "short": "2 moves", "long": "3 moves", "veryLong": "4+ moves",
    "master": "Master game", "masterVsMaster": "Master vs master", "superGM": "Super GM game",
    "enPassant": "En passant",
}
# shown after the motifs: what it wins, the game phase, length, whose game
_LATE_THEMES = {"crushing", "advantage", "equality", "opening", "middlegame", "endgame", "oneMove",
                "short", "long", "veryLong", "master", "masterVsMaster", "superGM", "mate"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def key(p: Puzzle) -> str:
    if p.source == PuzzleSource.LICHESS:
        return f"lichess:{p.id}"
    return f"{p.fen}|{p.correct_move_uci}"


def theme_name(theme: str) -> str:
    """Lichess theme id -> name, e.g. 'hangingPiece' -> 'Hanging piece'."""
    if theme in _THEME_NAMES:
        return _THEME_NAMES[theme]
    words = "".join(" " + c.lower() if c.isupper() else c for c in theme)
    return words[:1].upper() + words[1:]


def auto_tags(p: Puzzle, sharp: bool, endgame: bool) -> List[str]:
    """The puzzle's own tags, most telling first, no repeats."""
    tags: List[str] = []
    if p.kind:
        tags.append(p.kind)
    if p.source == PuzzleSource.LICHESS:
        themes = (p.themes or "").split()
        tags += [theme_name(t) for t in sorted(themes, key=lambda t: t in _LATE_THEMES)]
    else:
        if p.puzzle_type:
            tags.append(p.puzzle_type)
        moves = p.solution_moves or 1
        tags.append("One move" if moves == 1 else f"{moves} moves")
        tags.append("Sharp" if sharp else "Quiet")
        if endgame:
            tags.append("Endgame")
    seen: Set[str] = set()
    return [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]


# ---------- your tags ----------

def user_tags(conn: sqlite3.Connection, p: Puzzle) -> List[str]:
    return [r[0] for r in conn.execute(
        "SELECT tag FROM user_tags WHERE puzzle_key = ? ORDER BY added_at", (key(p),))]


def add(conn: sqlite3.Connection, p: Puzzle, tag: str) -> None:
    tag = " ".join(tag.split())[:40]
    if tag:
        with conn:
            conn.execute("INSERT OR IGNORE INTO user_tags (puzzle_key, tag, added_at) VALUES (?, ?, ?)",
                         (key(p), tag, dt.datetime.now().isoformat()))


def remove(conn: sqlite3.Connection, p: Puzzle, tag: str) -> None:
    with conn:
        conn.execute("DELETE FROM user_tags WHERE puzzle_key = ? AND tag = ?", (key(p), tag))


def all_user_tags(conn: sqlite3.Connection) -> Dict[str, Set[str]]:
    """tag -> puzzle keys carrying it (for the queue's tag filter)."""
    out: Dict[str, Set[str]] = {}
    for k, tag in conn.execute("SELECT puzzle_key, tag FROM user_tags"):
        out.setdefault(tag, set()).add(k)
    return out
