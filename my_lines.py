"""How you've done in each named opening line (the opening library), from
your whole game history — Opening Drill → Add lines → Opening library.

Each game gets the deepest named line it reached (matched by position, so
transpositions count — the way Lichess names a game's opening), saved in
game_lines so only new games are matched later. Per line and colour:
  - your results;
  - from your analyzed games: how accurately you played the opening (average
    pawns lost per move over your first OPENING_PLIES/2 moves), blunders there,
    and the evaluation when the opening is over (your point of view).

"Played well" ranks by a score that doesn't over-trust a handful of games
(your points shrunk toward 50% by PRIOR_GAMES), then accuracy.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import chess

from analyzer import MATE_CP
from repertoire_gaps import _opening_moves, position_key
from stats import Record

MAX_PLY = 30               # how deep a game is matched against the library
OPENING_PLIES = 30         # the opening, for accuracy and "coming out of it": your first 15 moves
BLUNDER_CP = 300
PRIOR_GAMES = 5
MIN_GAMES = 3              # fewer: shown, but not ranked
VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS game_lines (
    game_id TEXT PRIMARY KEY,
    library_id INTEGER,       -- NULL: no named line reached
    plies INTEGER,
    version INTEGER
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass
class LineRecord:
    library_id: int
    color: str
    record: Record = field(default_factory=Record)
    game_ids: List[str] = field(default_factory=list)
    loss_sum: int = 0          # centipawns lost over your opening moves (analyzed games)
    loss_moves: int = 0
    blunders: int = 0
    eval_sum: int = 0          # evaluation when the opening's over (analyzed games)
    eval_games: int = 0

    @property
    def games(self) -> int:
        return self.record.games

    @property
    def avg_loss(self) -> Optional[float]:
        return self.loss_sum / self.loss_moves / 100 if self.loss_moves else None

    @property
    def eval_out(self) -> Optional[float]:
        return self.eval_sum / self.eval_games / 100 if self.eval_games else None

    @property
    def rating(self) -> float:
        """Your score, shrunk toward 50% for few games."""
        r = self.record
        return (r.wins + 0.5 * r.draws + 0.5 * PRIOR_GAMES) / (r.games + PRIOR_GAMES)


# ---------- matching (background thread: its own connection) ----------

def library_positions(conn: sqlite3.Connection) -> Dict[str, Tuple[int, int]]:
    """position -> (library id, plies), the shortest named line for each position."""
    out: Dict[str, Tuple[int, int]] = {}
    for r in conn.execute("SELECT id, ucis, plies FROM opening_library ORDER BY plies, id"):
        board = chess.Board()
        for uci in r[1].split():
            board.push_uci(uci)
        out.setdefault(position_key(board.fen()), (r[0], r[2]))
    return out


def match_game(pgn: str, positions: Dict[str, Tuple[int, int]]) -> Tuple[Optional[int], int]:
    board, best = chess.Board(), (None, 0)
    for i, san in enumerate(_opening_moves(pgn, MAX_PLY)):
        try:
            board.push_san(san)
        except ValueError:
            break
        hit = positions.get(position_key(board.fen()))
        if hit is not None:
            best = (hit[0], i + 1)
    return best


def match_all(db_path: str, game_ids: List[str]) -> List[Tuple[str, Optional[int], int]]:
    """For the background thread: opens its own read-only connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        positions = library_positions(conn)
        out = []
        for i in range(0, len(game_ids), 500):
            chunk = game_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for gid, pgn in conn.execute(f"SELECT id, pgn FROM games WHERE id IN ({marks})", chunk):
                lid, plies = match_game(pgn, positions)
                out.append((gid, lid, plies))
        return out
    finally:
        conn.close()


def db_path(conn: sqlite3.Connection) -> str:
    return next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")


def unmatched(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute(
        "SELECT g.id FROM games g LEFT JOIN game_lines l ON l.game_id = g.id AND l.version = ? "
        "WHERE l.game_id IS NULL", (VERSION,))]


def save(conn: sqlite3.Connection, rows: List[Tuple[str, Optional[int], int]]) -> None:
    with conn:
        conn.executemany("INSERT OR REPLACE INTO game_lines (game_id, library_id, plies, version) VALUES (?, ?, ?, ?)",
                         [(g, l, p, VERSION) for g, l, p in rows])


# ---------- your record per line ----------

def records(conn: sqlite3.Connection) -> Dict[Tuple[int, str], LineRecord]:
    out: Dict[Tuple[int, str], LineRecord] = {}
    owner: Dict[str, LineRecord] = {}
    for r in conn.execute("SELECT l.game_id, l.library_id, g.my_color, g.result FROM game_lines l "
                          "JOIN games g ON g.id = l.game_id WHERE l.library_id IS NOT NULL"):
        key = (r[1], r[2])
        rec = out.setdefault(key, LineRecord(r[1], r[2]))
        rec.record.add(r[3])
        rec.game_ids.append(r[0])
        owner[r[0]] = rec
    # the opening, from the saved analysis: your moves (only those are evaluated)
    last_eval: Dict[str, int] = {}
    for gid, ply, before, after in conn.execute(
            "SELECT game_id, ply, cp_before, cp_after FROM move_evaluations WHERE ply < ? ORDER BY game_id, ply",
            (OPENING_PLIES,)):
        rec = owner.get(gid)
        if rec is None or after is None:
            continue
        loss = max(0, min(before, MATE_CP) - max(after, -MATE_CP))
        rec.loss_sum += min(loss, 1000)             # one hung queen shouldn't swamp the average
        rec.loss_moves += 1
        rec.blunders += loss >= BLUNDER_CP
        last_eval[gid] = max(-1000, min(1000, after))
    for gid, cp in last_eval.items():
        owner[gid].eval_sum += cp
        owner[gid].eval_games += 1
    return out


def by_library_id(recs: Dict[Tuple[int, str], LineRecord]) -> Dict[int, List[LineRecord]]:
    out: Dict[int, List[LineRecord]] = defaultdict(list)
    for (lid, _), rec in recs.items():
        out[lid].append(rec)
    for lid in out:
        out[lid].sort(key=lambda r: -r.games)
    return out


def describe(rec: LineRecord) -> str:
    r = rec.record
    parts = [f"As {rec.color}: {rec.games} game{'s' if rec.games != 1 else ''}, "
             f"{r.score:.0%} ({r.wins}W {r.draws}D {r.losses}L)"]
    if rec.avg_loss is not None:
        parts.append(f"opening accuracy: {rec.avg_loss:.2f} pawns lost per move")
    if rec.blunders:
        parts.append(f"{rec.blunders} opening blunder{'s' if rec.blunders != 1 else ''}")
    if rec.eval_out is not None:
        parts.append(f"you come out of the opening at {rec.eval_out:+.1f} on average".replace("-", "−"))
    return " · ".join(parts)
