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
    """What the side to move could win from `me` right now (one capture, defended or not).
    Only your pieces under attack are looked at, and only those captures checked for
    legality — not every legal move in the position (this runs for ~40,000 positions)."""
    best = 0
    them = board.turn
    for square in chess.SquareSet(board.occupied_co[me]):
        victim = board.piece_at(square)
        if victim is None or victim.piece_type == chess.KING:
            continue
        value = VALUES[victim.piece_type]
        if value <= best:
            continue                                   # can't beat what's already found
        defended = board.is_attacked_by(me, square)
        for attacker_sq in board.attackers(them, square):
            attacker = board.piece_at(attacker_sq)
            move = chess.Move(attacker_sq, square)
            if attacker.piece_type == chess.PAWN and chess.square_rank(square) in (0, 7):
                move.promotion = chess.QUEEN
            if not board.is_legal(move):
                continue
            gain = value - (VALUES[attacker.piece_type] if defended else 0)
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


def _find_all_direct(conn: sqlite3.Connection) -> List[Amazing]:
    """Every amazing move in your analyzed games, newest games first (no cache)."""
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


def _find_missed_direct(conn: sqlite3.Connection) -> List[Missed]:
    """Every sound sacrifice you missed in your analyzed games, newest games first (no cache)."""
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


# ---------- saved results (a game's analysis never changes: each game is searched once) ----------

CACHE_VERSION = 1          # bump when the rules above change: every game is searched again
SCAN_YIELD_EVERY = 300     # positions between short pauses (lets the window breathe meanwhile)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sac_scanned (
    game_id TEXT PRIMARY KEY,
    version INTEGER,
    evals INTEGER              -- how many analyzed moves it had (re-analyzed: searched again)
);
CREATE TABLE IF NOT EXISTS sac_found (
    game_id TEXT,
    ply INTEGER,
    kind TEXT,                 -- "amazing" / "missed"
    data TEXT                  -- the Amazing / Missed, as JSON
);
CREATE INDEX IF NOT EXISTS idx_sac_found_game ON sac_found (game_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _to_scan(conn: sqlite3.Connection) -> List[str]:
    done = {r[0]: r[1] for r in conn.execute("SELECT game_id, evals FROM sac_scanned WHERE version = ?",
                                             (CACHE_VERSION,))}
    return [gid for gid, n in conn.execute("SELECT game_id, COUNT(*) FROM move_evaluations GROUP BY game_id")
            if done.get(gid) != n]


def _scan(conn: sqlite3.Connection, game_ids: List[str]) -> None:
    """Search these games and save what's found (works from a background thread's own connection)."""
    import json
    import time
    from dataclasses import asdict
    for i in range(0, len(game_ids), 200):
        chunk = game_ids[i:i + 200]
        marks = ",".join("?" * len(chunk))
        found, counts, seen = [], {}, 0
        for r in conn.execute(
                f"""SELECT game_id, ply, fen_before, move_uci, best_move_uci, cp_before, cp_after
                    FROM move_evaluations WHERE game_id IN ({marks})""", chunk):
            gid, ply, fen, uci, best, before, after = r
            counts[gid] = counts.get(gid, 0) + 1
            seen += 1
            if seen % SCAN_YIELD_EVERY == 0:
                time.sleep(0.002)                   # a breath for the window (one Python thread at a time)
            if after is None:
                continue
            size = is_amazing(fen, uci, before, after)
            if size:
                san = chess.Board(fen).san(chess.Move.from_uci(uci))
                found.append((gid, ply, "amazing", json.dumps(asdict(Amazing(gid, ply, san, size, before, after, fen, uci)))))
            size = missed_sacrifice(fen, uci, best, before, after)
            if size:
                board = chess.Board(fen)
                m = Missed(gid, ply, fen, best, board.san(chess.Move.from_uci(best)),
                           board.san(chess.Move.from_uci(uci)), size, before, after)
                found.append((gid, ply, "missed", json.dumps(asdict(m))))
        with conn:
            conn.execute(f"DELETE FROM sac_found WHERE game_id IN ({marks})", chunk)
            conn.executemany("INSERT INTO sac_found (game_id, ply, kind, data) VALUES (?, ?, ?, ?)", found)
            conn.executemany("INSERT OR REPLACE INTO sac_scanned (game_id, version, evals) VALUES (?, ?, ?)",
                             [(g, CACHE_VERSION, counts.get(g, 0)) for g in chunk])


def _cached(conn: sqlite3.Connection, kind: str, cls):
    import json
    _scan(conn, _to_scan(conn))                     # only games not searched yet (or re-analyzed)
    return [cls(**json.loads(r[0])) for r in conn.execute(
        """SELECT f.data FROM sac_found f JOIN games g ON g.id = f.game_id WHERE f.kind = ?
           ORDER BY g.played_at DESC, f.ply""", (kind,))]


def find_all(conn: sqlite3.Connection) -> List[Amazing]:
    """Every amazing move in your analyzed games, newest games first — from the saved
    search (new games are searched first). Needs a connection that can write."""
    return _cached(conn, "amazing", Amazing)


def find_missed(conn: sqlite3.Connection, cached: bool = True) -> List[Missed]:
    """Every sound sacrifice you missed, newest games first (see find_all).
    cached=False: search directly, saving nothing (the one-off puzzle backfill)."""
    return _cached(conn, "missed", Missed) if cached else _find_missed_direct(conn)


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
