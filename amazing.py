"""Amazing moves (!!) and accuracy, from your saved analysis — no engine run.

A missed sacrifice is the other side of it (see missed_sacrifice): Stockfish's
best move was a sound piece sacrifice and you played something else that
cost you.

An amazing move is chess.com's idea of a "brilliant" one, by its published
definition (chess.com doesn't share its own labels through its API): a piece
sacrifice that is the best or nearly-best move, made when you weren't already
completely winning, that doesn't leave you in a bad position. Concretely,
for one of your analyzed moves:
  - within NEAR_BEST_CP of Stockfish's best;
  - you weren't already winning by WINNING_CP or more, and aren't worse than
    BAD_AFTER_CP afterwards;
  - a piece move (not a pawn or the king) that newly offers at least
    MIN_OFFER pawns' worth: what your opponent could now take, minus what the
    move itself captured, minus anything they could already take before it.

Accuracy: Lichess's per-move formula from your winning chances before and
after each move, combined over your analyzed moves the way Lichess does
(plain average and harmonic mean, half each).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

import chess

from analyzer import MATE_CP, win_chances
from explain import VALUES

NEAR_BEST_CP = 40
WINNING_CP = 500
BAD_AFTER_CP = -150
MIN_OFFER = 2
# a missed sacrifice: Stockfish's best was a sound sacrifice (you'd not be worse after it), you
# weren't already winning big, and what you played instead lost at least this much winning chance
MISSED_MIN_CP = -30
MISSED_MIN_LOST = 0.1


@dataclass
class Amazing:
    game_id: str
    ply: int
    san: str
    offered: int               # pawns' worth newly offered
    cp_before: int             # your point of view, with the best move
    cp_after: int
    fen: str = ""              # the position before it
    uci: str = ""


def _best_capture(board: chess.Board, me: chess.Color) -> int:
    """What the side to move could win from `me` right now (one capture, defended or not)."""
    best = 0
    for m in board.legal_moves:
        if not board.is_capture(m) or board.is_en_passant(m):
            continue
        victim, attacker = board.piece_at(m.to_square), board.piece_at(m.from_square)
        if victim is None or attacker is None or victim.color != me:
            continue
        gain = VALUES[victim.piece_type] - (VALUES[attacker.piece_type] if board.is_attacked_by(me, m.to_square) else 0)
        best = max(best, gain)
    return best


def offered(board: chess.Board, move: chess.Move) -> int:
    """Pawns' worth this move newly puts en prise (0 if it isn't a sacrifice)."""
    me = board.turn
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
        return 0
    victim = board.piece_at(move.to_square) if board.is_capture(move) else None
    took = VALUES[victim.piece_type] if victim is not None else 0
    before = board.copy(stack=False)
    before.push(chess.Move.null())            # what they could already take, before your move
    already = _best_capture(before, me) if before.is_valid() else 0
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return 0
    return max(0, _best_capture(after, me) - took - already)


def is_amazing(fen: str, move_uci: str, cp_before: Optional[int], cp_after: Optional[int]) -> int:
    """Pawns' worth sacrificed if this is an amazing move, else 0."""
    if cp_before is None or cp_after is None:
        return 0
    if cp_before - cp_after > NEAR_BEST_CP or cp_before >= WINNING_CP or cp_after <= BAD_AFTER_CP:
        return 0
    size = offered(chess.Board(fen), chess.Move.from_uci(move_uci))
    return size if size >= MIN_OFFER else 0


def find_all(conn: sqlite3.Connection) -> List[Amazing]:
    """Every amazing move in your analyzed games, newest games first."""
    out = []
    for r in conn.execute(
            """SELECT e.game_id, e.ply, e.fen_before, e.move_uci, e.cp_before, e.cp_after FROM move_evaluations e
               JOIN games g ON g.id = e.game_id
               WHERE e.cp_after IS NOT NULL AND e.cp_before - e.cp_after <= ? AND e.cp_before < ? AND e.cp_after > ?
               ORDER BY g.played_at DESC, e.ply""", (NEAR_BEST_CP, WINNING_CP, BAD_AFTER_CP)):
        size = is_amazing(r[2], r[3], r[4], r[5])
        if size:
            board = chess.Board(r[2])
            out.append(Amazing(r[0], r[1], board.san(chess.Move.from_uci(r[3])), size, r[4], r[5], r[2], r[3]))
    return out


# ---------- missed sacrifices ----------

@dataclass
class Missed:
    game_id: str
    ply: int
    fen: str                   # the position (your move)
    best_uci: str              # the sacrifice you missed
    best_san: str
    played_san: str            # what you played instead
    offered: int
    cp_before: int             # with the sacrifice, your point of view
    cp_after: int              # after what you played


def missed_sacrifice(fen: str, played_uci: str, best_uci: Optional[str], cp_before: Optional[int],
                     cp_after: Optional[int]) -> int:
    """Pawns' worth Stockfish's best move would have sacrificed, if you missed a
    sound sacrifice that mattered; else 0."""
    if best_uci is None or best_uci == played_uci or cp_before is None or cp_after is None:
        return 0
    if cp_before >= WINNING_CP or cp_before < MISSED_MIN_CP:
        return 0
    if win_chances(cp_before) - win_chances(cp_after) < MISSED_MIN_LOST:
        return 0                                          # your move was about as good
    size = offered(chess.Board(fen), chess.Move.from_uci(best_uci))
    return size if size >= MIN_OFFER else 0


def find_missed(conn: sqlite3.Connection) -> List[Missed]:
    """Every sound sacrifice you missed in your analyzed games, newest games first."""
    out = []
    for r in conn.execute(
            """SELECT e.game_id, e.ply, e.fen_before, e.move_uci, e.best_move_uci, e.cp_before, e.cp_after
               FROM move_evaluations e JOIN games g ON g.id = e.game_id
               WHERE e.cp_after IS NOT NULL AND e.best_move_uci IS NOT NULL AND e.move_uci != e.best_move_uci
                 AND e.cp_before < ? AND e.cp_before >= ?
               ORDER BY g.played_at DESC, e.ply""", (WINNING_CP, MISSED_MIN_CP)):
        size = missed_sacrifice(r[2], r[3], r[4], r[5], r[6])
        if size:
            board = chess.Board(r[2])
            out.append(Missed(r[0], r[1], r[2], r[4], board.san(chess.Move.from_uci(r[4])),
                              board.san(chess.Move.from_uci(r[3])), size, r[5], r[6]))
    return out


# ---------- accuracy ----------

def move_accuracy(cp_before: int, cp_after: int) -> float:
    """Lichess's formula: 100 = you kept all your winning chances."""
    wp_before = 50 + 50 * win_chances(max(-MATE_CP, min(MATE_CP, cp_before)))
    wp_after = 50 + 50 * win_chances(max(-MATE_CP, min(MATE_CP, cp_after)))
    acc = 103.1668100711649 * math.exp(-0.04354415386753951 * max(0.0, wp_before - wp_after)) - 3.166924740191411
    return max(0.0, min(100.0, acc))


def game_accuracy(evals: Dict[int, object]) -> Optional[float]:
    """Your accuracy in one game, the way Lichess combines its moves: half the
    plain average, half the harmonic mean (which a blunder drags down hard)."""
    accs = [move_accuracy(e["cp_before"], e["cp_after"]) for e in evals.values() if e["cp_after"] is not None]
    if not accs:
        return None
    mean = sum(accs) / len(accs)
    harmonic = len(accs) / sum(1 / max(a, 1.0) for a in accs)
    return (mean + harmonic) / 2
