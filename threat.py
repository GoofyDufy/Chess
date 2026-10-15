"""What's the threat? — a drill for the habit most mistakes come from: not
noticing what your opponent's last move threatens.

A round: a position from your games right after your opponent's move.
  1. Name the threat: play the move your opponent wants to make next (the
     board lets you move their pieces), or say "No threat".
  2. Answer it: your move in the real position, judged by Stockfish.

The threat is Stockfish's: if you could pass, what would your opponent
gain? (the null-move test puzzle_kind.py uses for Defensive puzzles). Any
of their moves that would gain at least THREAT_CP counts. About 1 in 4
positions have no threat (they'd gain under NO_THREAT_CP), so "there's
always something" isn't a strategy.

Positions with a threat come from your puzzles that face one (Defensive /
Counterattack, not in check — a check is no puzzle to spot); positions
without one from quiet moments of your games.
"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import chess
import chess.engine

import spot
from analyzer import MATE_CP, _score_to_cp
from explain import VALUES

THREAT_CP = 150          # what a threat must be worth to your opponent (1.5 pawns)
NO_THREAT_CP = 50        # "no threat": passing would cost you less than this
NO_THREAT_SHARE = 1 / 4
DEPTH = 12
THREAT_LINES = 3         # their best moves after your pass, checked as threats
MOVE_OK_CP = spot.MOVE_OK_CP   # your answer: within half a pawn of the best move
FOUND_XP = 3             # named the threat (or rightly said there's none)
ANSWER_XP = 5            # a good answer
BATCH = 40

SCHEMA = """
CREATE TABLE IF NOT EXISTS threat_attempts (
    fen TEXT,
    had_threat INTEGER,
    found INTEGER,             -- named a real threat, or rightly said "no threat"
    move_ok INTEGER,
    attempted_at TEXT
);
"""

_NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook",
          chess.QUEEN: "queen", chess.KING: "king"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass
class Threat:
    move: chess.Move           # the opponent's move, played after your pass
    gain: int                  # what it gains them, centipawns
    mate_in: Optional[int] = None


@dataclass
class Assessment:
    threats: List[Threat]      # most dangerous first; empty = no threat
    best: chess.Move           # your best answer in the real position
    best_cp: int               # its evaluation, your point of view
    pv: List[chess.Move] = field(default_factory=list)

    @property
    def has_threat(self) -> bool:
        return bool(self.threats)


def passed(board: chess.Board) -> chess.Board:
    """The position with you passing: your opponent to move (no history)."""
    b = board.copy(stack=False)
    b.push(chess.Move.null())
    return chess.Board(b.fen())


def describe(board: chess.Board, threat: Threat) -> str:
    """'...Nxe4, taking your knight' — board is the real position (you to move)."""
    after_pass = passed(board)
    san = after_pass.san(threat.move)
    dots = "..." if after_pass.turn == chess.BLACK else ""
    if threat.mate_in:
        what = "with mate" if threat.mate_in == 1 else f"forcing mate in {threat.mate_in}"
    elif after_pass.is_capture(threat.move) and after_pass.piece_at(threat.move.to_square):
        what = f"taking your {_NAMES[after_pass.piece_at(threat.move.to_square).piece_type]}"
    else:
        what = f"winning about {threat.gain / 100:.1f} pawns"
    return f"{dots}{san}, {what}"


# ---------- picking positions ----------

@dataclass
class Candidate:
    game_id: str
    ply: int
    fen: str
    played_uci: str            # what you played in the game
    expect_threat: bool


def candidates(conn: sqlite3.Connection, want_threat: bool, exclude: set, limit: int = BATCH) -> List[Candidate]:
    if want_threat:
        rows = conn.execute(
            """SELECT e.game_id, e.ply, e.fen_before, e.move_uci FROM puzzles p
               JOIN move_evaluations e ON e.game_id = p.source_game_id AND e.fen_before = p.fen
               WHERE p.kind IN ('Defensive', 'Counterattack')
               ORDER BY RANDOM() LIMIT ?""", (limit,)).fetchall()
    else:
        rows = [(c.game_id, c.ply, c.fen, c.played_uci) for c in spot.candidates(conn, True, exclude, limit)]
    out = []
    for game_id, ply, fen, played in rows:
        if fen in exclude or chess.Board(fen).is_check():
            continue
        out.append(Candidate(game_id, ply, fen, played, want_threat))
    return out


def want_threat() -> bool:
    return random.random() >= NO_THREAT_SHARE


# ---------- engine work (background thread only) ----------

def assess(engine: chess.engine.SimpleEngine, board: chess.Board) -> Optional[Assessment]:
    """The threats in a position (you to move) and your best answer. None if
    it's neither a clear threat nor clearly calm (it isn't used)."""
    me = board.turn
    info = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
    pv = list(info.get("pv", []))
    if not pv:
        return None
    now = _score_to_cp(info["score"], me)
    after_pass = passed(board)
    if not after_pass.is_valid():
        return None
    threats, worst = [], None
    for line in engine.analyse(after_pass, chess.engine.Limit(depth=DEPTH), multipv=THREAT_LINES):
        if not line.get("pv"):
            continue
        gain = now - _score_to_cp(line["score"], me)          # what passing costs you, via this move
        worst = gain if worst is None else max(worst, gain)
        if gain >= THREAT_CP:
            pov = line["score"].pov(not me)
            threats.append(Threat(line["pv"][0], gain, pov.mate() if pov.is_mate() and pov.mate() > 0 else None))
    if worst is None or (not threats and worst >= NO_THREAT_CP):
        return None                                           # a small threat: neither kind
    return Assessment(threats, pv[0], now, pv)


def judge_threat(engine: chess.engine.SimpleEngine, board: chess.Board, assessment: Assessment,
                 move: chess.Move) -> Tuple[bool, int]:
    """Your guess at their threat (their move, after your pass): (a real threat?, its gain)."""
    for t in assessment.threats:
        if t.move == move:
            return True, t.gain
    after = passed(board)
    after.push(move)
    if after.is_checkmate():
        return True, MATE_CP
    info = engine.analyse(after, chess.engine.Limit(depth=DEPTH))
    gain = assessment.best_cp - _score_to_cp(info["score"], board.turn)
    return gain >= THREAT_CP, gain


def judge_answer(engine: chess.engine.SimpleEngine, board: chess.Board, assessment: Assessment,
                 move: chess.Move) -> Tuple[bool, int]:
    """Your move in the real position: (good?, centipawns worse than the best)."""
    if move == assessment.best:
        return True, 0
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return True, 0
    info = engine.analyse(after, chess.engine.Limit(depth=DEPTH))
    cp = _score_to_cp(info["score"], board.turn)
    loss = max(0, assessment.best_cp - cp)
    if assessment.best_cp >= MATE_CP and cp >= MATE_CP:
        loss = 0
    return loss <= MOVE_OK_CP, loss


# ---------- results ----------

def record(conn: sqlite3.Connection, fen: str, had_threat: bool, found: bool, move_ok: bool) -> None:
    conn.execute("INSERT INTO threat_attempts (fen, had_threat, found, move_ok, attempted_at) VALUES (?, ?, ?, ?, ?)",
                 (fen, int(had_threat), int(found), int(move_ok), dt.datetime.now().isoformat()))
    conn.commit()


def xp(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(found), 0), COALESCE(SUM(move_ok), 0) FROM threat_attempts").fetchone()
    return row[0] * FOUND_XP + row[1] * ANSWER_XP


@dataclass
class Summary:
    rounds: int = 0
    threats: int = 0
    found: int = 0             # threats named
    calm: int = 0
    calm_right: int = 0        # "no threat" rightly said
    answers_ok: int = 0


def summary(conn: sqlite3.Connection, since: Optional[str] = None) -> Summary:
    s = Summary()
    for had, found, ok in conn.execute(
            "SELECT had_threat, found, move_ok FROM threat_attempts WHERE attempted_at >= ?", (since or "",)):
        s.rounds += 1
        s.answers_ok += ok
        if had:
            s.threats += 1
            s.found += found
        else:
            s.calm += 1
            s.calm_right += found
    return s


def seen_fens(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT fen FROM threat_attempts")}
