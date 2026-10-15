"""What works in practice at a position, against players around your level
(Opening Drill → My games, under a deviation's "why").

For each move worth considering — the ones Lichess players at your rating
play most, plus your prep move and your own — three things:
  - is it sound? Stockfish's evaluation after it (your point of view);
  - how often players at your level choose it, and how they score with it;
  - does it get you somewhere? What your opponents at your level usually
    reply (their share), and how often those replies leave you +0.5 or
    better by Stockfish ("advantage rate").
Moves are ranked by advantage rate, then the average evaluation after their
usual replies (steadier: early on most replies leave you +0.2 to +0.5, so the
+0.5 line alone flips on small differences), then score; unsound ones
(Stockfish has you worse than UNSOUND_CP after them) go last.

Explorer data comes from the cache or is downloaded first by the caller's
background thread (explorer.fetch_stats, never from here); the engine part
runs on that thread too. Results are saved in practical_moves.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import chess
import chess.engine

from analyzer import MATE_CP, _score_to_cp
from explorer import PositionStats

ADVANTAGE_CP = 50          # "an advantage": half a pawn or more
UNSOUND_CP = -30           # worse than this after the move: not recommended
MIN_GAMES = 30             # a popular move needs this many games at your level
MAX_POPULAR = 5            # popular moves considered (plus your prep and your move)
REPLY_SHARE = 0.05         # opponent replies under this share are ignored
MAX_REPLIES = 6
DEPTH = 16                 # replies within a few tenths: depth 14 swings too much
VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS practical_moves (
    key TEXT PRIMARY KEY,          -- rating bands | position | your move
    version INTEGER,
    data TEXT,                     -- [Choice] as JSON
    created_at TEXT
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass
class Reply:
    san: str
    share: float               # of their games at your level after your move
    cp: int                    # Stockfish after it, your point of view


@dataclass
class Choice:
    uci: str
    san: str
    eval_cp: int               # after your move (their best reply), your point of view
    share: Optional[float]     # how often players at your level choose it (None: no data)
    games: int
    score: Optional[float]     # how they score with it (your colour)
    advantage_rate: Optional[float]   # share of their usual replies leaving you +0.5 or better
    avg_cp: Optional[int]      # average evaluation after their usual replies
    replies: List[Reply] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)          # "your prep", "you played", "Stockfish's best"

    @property
    def sound(self) -> bool:
        return self.eval_cp >= UNSOUND_CP


def candidates(board: chess.Board, stats: Optional[PositionStats], prep: List[str], mine: str) -> List[str]:
    """ucis to look at: the popular moves at your level, your prep, your move."""
    out = []
    if stats is not None:
        for m in sorted(stats.moves, key=lambda m: -m.games):
            if m.games >= MIN_GAMES and len(out) < MAX_POPULAR:
                out.append(m.uci)
    for uci in prep + [mine]:
        if uci not in out:
            out.append(uci)
    return [u for u in out if chess.Move.from_uci(u) in board.legal_moves]


def evaluate(engine: chess.engine.SimpleEngine, board: chess.Board, ucis: List[str],
             stats: Optional[PositionStats], after_stats: Dict[str, Optional[PositionStats]],
             prep: List[str], mine: str) -> List[Choice]:
    """Engine work (background thread). after_stats: uci -> explorer stats after that move."""
    me = board.turn
    by_uci = {m.uci: m for m in stats.moves} if stats is not None else {}
    total = stats.games if stats is not None else 0
    choices = []
    for uci in ucis:
        move = chess.Move.from_uci(uci)
        after = board.copy()
        after.push(move)
        if after.is_game_over():
            continue
        best = engine.analyse(after, chess.engine.Limit(depth=DEPTH))
        eval_cp = _score_to_cp(best["score"], me)
        m = by_uci.get(uci)
        a_stats = after_stats.get(uci)
        replies: List[Reply] = []
        if a_stats is not None and a_stats.games:
            usual = [r for r in sorted(a_stats.moves, key=lambda r: -r.games)
                     if r.games / a_stats.games >= REPLY_SHARE][:MAX_REPLIES]
            roots = [chess.Move.from_uci(r.uci) for r in usual if chess.Move.from_uci(r.uci) in after.legal_moves]
            if roots:
                infos = engine.analyse(after, chess.engine.Limit(depth=DEPTH), multipv=len(roots), root_moves=roots)
                cp_of = {info["pv"][0].uci(): _score_to_cp(info["score"], me) for info in infos if info.get("pv")}
                for r in usual:
                    if r.uci in cp_of:
                        replies.append(Reply(after.san(chess.Move.from_uci(r.uci)), r.games / a_stats.games,
                                             cp_of[r.uci]))
        weight = sum(r.share for r in replies)
        adv = sum(r.share for r in replies if r.cp >= ADVANTAGE_CP) / weight if weight else None
        avg = (int(sum(r.share * max(-MATE_CP // 10, min(MATE_CP // 10, r.cp)) for r in replies) / weight)
               if weight else None)
        tags = (["your prep"] if uci in prep else []) + (["you played"] if uci == mine else [])
        choices.append(Choice(uci, board.san(move), eval_cp, (m.games / total) if m and total else None,
                              m.games if m else 0, m.score_for(me) if m else None, adv, avg, replies, tags))
    if choices:
        top = max(choices, key=lambda c: c.eval_cp)
        top.tags.append("Stockfish's best of these")
    return rank(choices)


def rank(choices: List[Choice]) -> List[Choice]:
    """Sound moves first, by how often they lead to +0.5, then by score."""
    return sorted(choices, key=lambda c: (not c.sound, -(c.advantage_rate if c.advantage_rate is not None else -1),
                                          -(c.avg_cp if c.avg_cp is not None else -MATE_CP),
                                          -(c.score if c.score is not None else 0), -c.eval_cp))


def describe(choices: List[Choice], band: str) -> str:
    if not choices:
        return ""
    lines = [f"What works against players {band} here (ranked by how often their usual replies "
             f"leave you +{ADVANTAGE_CP / 100:.1f} or better):"]
    for i, c in enumerate(choices, 1):
        bits = [f"Stockfish {c.eval_cp / 100:+.2f}".replace("-", "−")]
        if c.share is not None:
            bits.append(f"played {c.share:.0%}, scores {c.score:.0%}")
        if c.advantage_rate is not None:
            bits.append(f"+0.5 or better after {c.advantage_rate:.0%} of their replies "
                        f"(average {c.avg_cp / 100:+.2f})".replace("-", "−"))
        tags = f" ({', '.join(c.tags)})" if c.tags else ""
        mark = "" if c.sound else "  — not sound"
        lines.append(f"{i}. {c.san}{tags}: " + " · ".join(bits) + mark)
        if c.replies and i <= 3:
            usual = ", ".join(f"{r.san} {r.share:.0%} → {r.cp / 100:+.1f}".replace("-", "−") for r in c.replies[:4])
            lines.append(f"    their usual replies: {usual}")
    best = next((c for c in choices if c.sound and c.advantage_rate is not None), None)
    if best is not None and best.advantage_rate == 0:
        lines.append("None of these usually gets you a half-pawn edge yet — the position is still balanced; "
                     "the ranking shows which keeps the most pressure.")
    elif best is not None and "your prep" not in best.tags:
        lines.append(f"In practice {best.san} gets you an edge most often here.")
    elif best is not None:
        lines.append(f"Your prep move {best.san} is also the one that works best in practice here.")
    return "\n".join(lines)


# ---------- cache ----------

def _key(bands: str, fen: str, mine: str) -> str:
    return f"{bands}|{' '.join(fen.split()[:4])}|{mine}"


def cached(conn: sqlite3.Connection, bands: str, fen: str, mine: str) -> Optional[List[Choice]]:
    row = conn.execute("SELECT data FROM practical_moves WHERE key = ? AND version = ?",
                       (_key(bands, fen, mine), VERSION)).fetchone()
    if row is None:
        return None
    out = []
    for c in json.loads(row[0]):
        c["replies"] = [Reply(**r) for r in c["replies"]]
        out.append(Choice(**c))
    return out


def save(conn: sqlite3.Connection, bands: str, fen: str, mine: str, choices: List[Choice]) -> None:
    conn.execute("INSERT OR REPLACE INTO practical_moves (key, version, data, created_at) VALUES (?, ?, ?, ?)",
                 (_key(bands, fen, mine), VERSION, json.dumps([asdict(c) for c in choices]), dt.datetime.now().isoformat()))
    conn.commit()
