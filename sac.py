"""Sacrifice!! — the third mode of Tactics & Threats: play a sacrifice, then
find the follow-up that makes it worth it, move by move, against Stockfish's
best defence, until the position is no longer sharp, you're clearly winning,
or it's mate.

Positions: your own amazing moves (amazing.py) where you're not worse after
the sacrifice, sacrifices you missed in your games (amazing.find_missed), and
Lichess puzzles with the "sacrifice" theme (their first
solution move is the sacrifice). A round is only used when there's at least
one real follow-up to find (often the position is calm right after their
reply: then there's nothing to train).

Rules (Stockfish, background thread):
  - their move: Stockfish's best defence;
  - your move is right if it keeps (nearly) all the winning chances of the
    best one (within OK_WC on Lichess's -1..1 scale), or mates;
  - the line ends, on your turn, when you're CLEAR_WIN_CP up, or the best
    move no longer stands out (2nd best within CALM_GAP: several good
    moves — no longer sharp), or after MAX_MOVES of yours.
"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import chess
import chess.engine

import amazing
from analyzer import MATE_CP, _score_to_cp, win_chances

DEPTH = 14
CALM_GAP = 0.12            # best vs 2nd best (winning chances): closer = several good moves = calm
OK_WC = 0.06               # your move may cost this much winning chance and still count
CLEAR_WIN_CP = 300
MAX_MOVES = 10             # follow-up moves of yours at most
MIN_AFTER_CP = -30         # your own sacrifices: not worse after it (they work)
CLEAN_XP, DONE_XP = 8, 3   # made it work without a miss / with help

SCHEMA = """
CREATE TABLE IF NOT EXISTS sac_attempts (
    key TEXT,                  -- the position + sacrifice
    source TEXT,               -- "game" / "lichess"
    moves INTEGER,             -- follow-up moves you had to find
    misses INTEGER,
    attempted_at TEXT
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass
class Candidate:
    fen: str                   # before the sacrifice (your move)
    sac_uci: str
    source: str                # "game" / "lichess"
    game_id: Optional[str] = None
    ply: Optional[int] = None
    label: str = ""            # where it's from

    @property
    def key(self) -> str:
        return f"{' '.join(self.fen.split()[:4])}|{self.sac_uci}"


@dataclass
class Step:
    """After their reply, your turn: whether the line goes on, and its best move."""
    over: bool
    reason: str = ""           # "mate" / "winning" / "calm" / "max" / "over"
    best: Optional[chess.Move] = None
    best_cp: int = 0           # your point of view
    pv: List[chess.Move] = field(default_factory=list)


# ---------- positions (main thread: database) ----------

def own_candidates(found: List[amazing.Amazing]) -> List[Candidate]:
    return [Candidate(a.fen, a.uci, "game", a.game_id, a.ply) for a in found if a.cp_after >= MIN_AFTER_CP]


def missed_candidates(missed: List[amazing.Missed]) -> List[Candidate]:
    """Sacrifices you missed in your games: now find them (source "missed")."""
    return [Candidate(m.fen, m.best_uci, "missed", m.game_id, m.ply) for m in missed]


def lichess_candidates(conn: sqlite3.Connection) -> List[Candidate]:
    out = []
    for r in conn.execute("SELECT id, fen, solution_ucis, rating FROM lichess_puzzles "
                          "WHERE ' ' || themes || ' ' LIKE '% sacrifice %'"):
        out.append(Candidate(r[1], r[2].split()[0], "lichess", label=f"Lichess puzzle, rated {r[3]}"))
    return out


def seen(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT key FROM sac_attempts")}


def pick(cands: List[Candidate], exclude: set) -> List[Candidate]:
    """Not done yet first, shuffled; your own games and Lichess mixed."""
    fresh = [c for c in cands if c.key not in exclude] or list(cands)
    random.shuffle(fresh)
    return fresh


# ---------- engine work (background thread only) ----------

def their_reply(engine: chess.engine.SimpleEngine, board: chess.Board) -> Optional[chess.Move]:
    if board.is_game_over():
        return None
    pv = engine.analyse(board, chess.engine.Limit(depth=DEPTH)).get("pv", [])
    return pv[0] if pv else None


def step(engine: chess.engine.SimpleEngine, board: chess.Board, me: chess.Color, moves_done: int) -> Step:
    """Your turn: is the line over, and what's the move to find?"""
    if board.is_checkmate():
        return Step(True, "mate")
    if board.is_game_over():
        return Step(True, "over")
    lines = engine.analyse(board, chess.engine.Limit(depth=DEPTH), multipv=2)
    cps = [_score_to_cp(l["score"], me) for l in lines if l.get("pv")]
    best = lines[0]["pv"][0]
    s = Step(False, best=best, best_cp=cps[0], pv=list(lines[0]["pv"]))
    if cps[0] >= CLEAR_WIN_CP and cps[0] < MATE_CP // 2:
        return Step(True, "winning", best, cps[0])
    if len(cps) > 1 and win_chances(cps[0]) - win_chances(cps[1]) < CALM_GAP and cps[0] < MATE_CP // 2:
        return Step(True, "calm", best, cps[0])
    if moves_done >= MAX_MOVES:
        return Step(True, "max", best, cps[0])
    return s


def judge(engine: chess.engine.SimpleEngine, board: chess.Board, move: chess.Move, s: Step) -> Tuple[bool, int]:
    """(right?, your evaluation after it)."""
    if move == s.best:
        return True, s.best_cp
    after = board.copy()
    after.push(move)
    if after.is_checkmate():
        return True, MATE_CP
    cp = _score_to_cp(engine.analyse(after, chess.engine.Limit(depth=DEPTH))["score"], board.turn)
    if s.best_cp >= MATE_CP // 2 and cp >= MATE_CP // 2:
        return True, cp                       # a slower mate is still a mate
    return win_chances(s.best_cp) - win_chances(cp) <= OK_WC, cp


def follow_ups(engine: chess.engine.SimpleEngine, cand: Candidate) -> int:
    """How many follow-up moves this round asks for (0: nothing to find — skip it)."""
    board = chess.Board(cand.fen)
    me = board.turn
    board.push_uci(cand.sac_uci)
    n = 0
    while True:
        reply = their_reply(engine, board)
        if reply is None:
            return n
        board.push(reply)
        s = step(engine, board, me, n)
        if s.over:
            return n
        board.push(s.best)
        n += 1
        if board.is_checkmate():
            return n


# ---------- results ----------

def record(conn: sqlite3.Connection, cand: Candidate, moves: int, misses: int) -> None:
    conn.execute("INSERT INTO sac_attempts (key, source, moves, misses, attempted_at) VALUES (?, ?, ?, ?, ?)",
                 (cand.key, cand.source, moves, misses, dt.datetime.now().isoformat()))
    conn.commit()


def xp(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(misses = 0), 0), COALESCE(SUM(misses > 0), 0) FROM sac_attempts").fetchone()
    return row[0] * CLEAN_XP + row[1] * DONE_XP


def summary(conn: sqlite3.Connection) -> Tuple[int, int]:
    """(rounds, clean rounds)."""
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(misses = 0), 0) FROM sac_attempts").fetchone()
    return row[0], row[1]
