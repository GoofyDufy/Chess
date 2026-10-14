"""Spot the Tactic: a game where each position from your own games first
asks "is there a tactic here?", then you play a move.

A position HAS a tactic when it's FORCING (checked by Stockfish when the
round is set up, not taken from the cache):
  - a forced mate in MAX_MATE moves or fewer, or
  - a forcing sequence that wins material: every one of your moves until
    the material is won is a check, a capture or a direct threat (see
    is_forcing), the gain is counted after the opponent's reply and has to
    hold to the end of the line.
A position is QUIET when there's no such win and the second-best move is
within MOVE_OK_CP of the best. Anything else — sharp but not tactical, like
an only-move defence — is "unclear" and never used. Positions are drawn
from two pools of your analyzed games — likely tactics (only-move
positions, and moments right after your opponent blundered) and likely
quiet ones (top three moves close together) — about QUIET_SHARE of rounds
quiet, so "yes" is the more common answer but can't be clicked blindly.

Trivial positions are skipped: being in check (the reply is often forced)
and plain recaptures (taking back on the square your opponent just
captured on), which would make "yes" obvious.

Your move: in a TACTIC position only Stockfish's best move counts (or a
different move that mates on the spot) — the point is finding the tactic.
In a QUIET position any move within MOVE_OK_CP of the best counts.
"""

from __future__ import annotations

import datetime as dt
import io
import random
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import chess
import chess.engine
import chess.pgn

from analyzer import MATE_CP, _score_to_cp
from explain import VALUES
from style import ONLY_MOVE_GAP_CP

QUIET_SHARE = 1 / 3
MIN_QUIET_PLY = 10            # quiet positions only after both sides have played 5 moves
                              # (earlier is opening theory — "no tactic" would be a given)
MIN_GAIN_PAWNS = 1            # material a forcing line must net
FORCING_PLIES = 8             # how far down the engine line a forcing win is looked for
MAX_MATE = 4                  # forced mates up to this many moves count
MAX_SOLUTION_MOVES = 4        # longest sequence you play (FORCING_PLIES covers it)
MIN_WINNING_CP = 100          # ...and the position must actually be good for you after it
MOVE_OK_CP = 50               # quiet positions: a move within half a pawn of the best is good
MAX_START_CP = 400            # skip positions already decided either way
CLASSIFY_DEPTH = 14
JUDGE_DEPTH = 12
LINE_DEPTH = 18               # the walk-through line (a time limit often stops with a 1-2 move line)
LINE_PLIES = 12               # moves shown after the best move
EVAL_DEPTH = 12               # evaluation bar along the line (~0.1 s a position)
CALL_XP = 3                   # right answer to the question
MOVE_XP = 5                   # a good move afterwards
BATCH = 40                    # candidates fetched per pool at a time


@dataclass
class Candidate:
    game_id: str
    ply: int
    fen: str
    played_uci: str               # what you played in the game


@dataclass
class Verdict:
    is_tactic: bool
    reason: str                   # "mate" / "material" / "quiet" / "unclear" (not used)
    best: chess.Move
    best_cp: int                  # your point of view
    second_cp: Optional[int]
    pv: List[chess.Move] = field(default_factory=list)
    mate_in: Optional[int] = None
    gain: int = 0                 # pawns won along the best line
    # what you play: the forcing line (your moves and the replies, ending with
    # yours, up to MAX_SOLUTION_MOVES of yours) — just [best] for quiet positions
    solution: List[chess.Move] = field(default_factory=list)

    @property
    def my_moves(self) -> int:
        return (len(self.solution) + 1) // 2

    def describe(self, board: chess.Board) -> str:
        best = board.san(self.best)
        if self.reason == "mate":
            return f"Yes — {best} forces mate in {self.mate_in}."
        if self.reason == "material":
            return f"Yes — {best} starts a forcing sequence that wins {_pawns(self.gain)}."
        return f"No tactic — several moves are about equally good (the engine's pick was {best})."


def _pawns(n: int) -> str:
    return {1: "a pawn", 2: "two pawns", 3: "a piece", 5: "a rook", 9: "a queen"}.get(n, f"{n} pawns' worth")


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUES[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())


# ---------- picking positions ----------

def candidates(conn: sqlite3.Connection, quiet: bool, exclude: set, limit: int = BATCH) -> List[Candidate]:
    """Random positions from your analyzed games that are likely quiet / likely
    tactical (the final call is classify()'s)."""
    if quiet:
        sql = """SELECT e.game_id, e.ply, e.fen_before, e.move_uci FROM move_evaluations e
                 WHERE e.cp_third IS NOT NULL AND e.cp_after IS NOT NULL
                   AND abs(e.cp_before) < 300 AND e.cp_before - e.cp_third < 50
                   AND e.cp_before - e.cp_after < 50 AND e.ply >= ?
                 ORDER BY RANDOM() LIMIT ?"""
        args: tuple = (MIN_QUIET_PLY, limit)
    else:
        # only-move positions, or your opponent's last move just handed you 2+ pawns
        sql = """SELECT e.game_id, e.ply, e.fen_before, e.move_uci FROM move_evaluations e
                 LEFT JOIN move_evaluations prev ON prev.game_id = e.game_id AND prev.ply = e.ply - 2
                 WHERE e.cp_second IS NOT NULL AND abs(e.cp_before) < ?
                   AND (e.cp_before - e.cp_second >= ?
                        OR (prev.cp_after IS NOT NULL AND e.cp_before - prev.cp_after >= 200))
                 ORDER BY RANDOM() LIMIT ?"""
        args = (MAX_START_CP, ONLY_MOVE_GAP_CP, limit)
    out = []
    for r in conn.execute(sql, args):
        if r[2] in exclude:
            continue
        out.append(Candidate(r[0], r[1], r[2], r[3]))
    return out


def last_move(conn: sqlite3.Connection, cand: Candidate) -> Tuple[Optional[chess.Move], int]:
    """The opponent's move that led to this position, and the value of the
    piece of yours it captured (0 if it wasn't a capture) — replayed from
    the game's PGN."""
    row = conn.execute("SELECT pgn FROM games WHERE id = ?", (cand.game_id,)).fetchone()
    game = chess.pgn.read_game(io.StringIO(row[0])) if row else None
    if game is None or cand.ply == 0:
        return None, 0
    board = game.board()
    for i, move in enumerate(game.mainline_moves()):
        if i == cand.ply - 1:
            if board.is_en_passant(move):
                return move, VALUES[chess.PAWN]
            victim = board.piece_at(move.to_square) if board.is_capture(move) else None
            return move, VALUES[victim.piece_type] if victim else 0
        board.push(move)
    return None, 0


def is_trivial(board: chess.Board, best: chess.Move, previous: Optional[chess.Move],
               previous_captured: int) -> bool:
    """In check (the reply is often forced), or the best move just takes back
    on the square the opponent captured on — "yes" would be obvious."""
    if board.is_check():
        return True
    return (previous is not None and previous_captured and board.is_capture(best)
            and best.to_square == previous.to_square)


# ---------- deciding & judging (engine calls: background thread only) ----------

def is_forcing(board: chess.Board, move: chess.Move) -> bool:
    """A check, a capture, or a NEW direct threat: after it, if you could
    move again you'd mate, or win a piece that's undefended or worth more
    than the piece taking it — and that wasn't already possible before the
    move (a piece that was hanging all along doesn't make any move forcing)."""
    if board.is_capture(move) or board.gives_check(move):
        return True
    after = board.copy(stack=False)
    after.push(move)
    after.push(chess.Move.null())        # "if it were your move again"
    before = _threats(board)
    return bool(_threats(after) - before)


def _threats(board: chess.Board) -> set:
    """What the side to move could do right now: 'mate', and the squares of
    enemy pieces it could win (undefended, or worth more than the taker)."""
    me, found = board.turn, set()
    for m in board.legal_moves:
        board.push(m)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            found.add("mate")
            continue
        if board.is_capture(m) and not board.is_en_passant(m):
            victim, attacker = board.piece_at(m.to_square), board.piece_at(m.from_square)
            if victim is None or attacker is None or victim.color == me:
                continue
            undefended = not board.is_attacked_by(not me, m.to_square)
            if undefended or VALUES[victim.piece_type] > VALUES[attacker.piece_type]:
                found.add(m.to_square)
    return found


def forcing_win(board: chess.Board, pv: List[chess.Move], just_lost: int = 0) -> Tuple[int, int]:
    """(material won, plies of the solution) for a forcing sequence along the
    engine line: every one of YOUR moves up to the win must be forcing; the
    gain is counted after the opponent's reply and must still stand at the
    end of the line looked at. The solution ends with your move that wins
    it. (0, 0) if the line isn't forcing or wins nothing."""
    me, b = board.turn, board.copy(stack=False)
    start = _material(b, me) + just_lost        # measured from before their capture
    won, length = 0, 0
    plies = min(FORCING_PLIES, len(pv))
    for i, move in enumerate(pv[:plies]):
        if move not in b.legal_moves:
            break
        if i % 2 == 0 and not won and not is_forcing(b, move):
            return 0, 0                  # a quiet move before anything was won
        b.push(move)
        if i % 2 == 1:                   # after the opponent's reply
            gain = _material(b, me) - start
            if not won and gain >= MIN_GAIN_PAWNS:
                won, length = gain, i    # your moves up to (not incl.) this reply
            elif won and gain < MIN_GAIN_PAWNS:
                return 0, 0              # given back
    return won, length


def forcing_gain(board: chess.Board, pv: List[chess.Move], just_lost: int = 0) -> int:
    return forcing_win(board, pv, just_lost)[0]


def mate_line(board: chess.Board, pv: List[chess.Move]) -> List[chess.Move]:
    """The engine line up to and including the mating move."""
    b, line = board.copy(stack=False), []
    for move in pv[:2 * MAX_SOLUTION_MOVES - 1]:
        if move not in b.legal_moves:
            break
        b.push(move)
        line.append(move)
        if b.is_checkmate():
            break
    return line


def solution_for(board: chess.Board, pv: List[chess.Move], score: chess.engine.PovScore,
                 just_lost: int = 0) -> List[chess.Move]:
    """The moves to play (yours and the opponent's replies, ending with
    yours): the forcing line to mate or to the material win, at most
    MAX_SOLUTION_MOVES of your moves — or just the first move when the
    position isn't a forcing tactic."""
    if not pv:
        return []
    pov = score.pov(board.turn)
    if pov.is_mate() and 0 < pov.mate() <= MAX_MATE:
        line = mate_line(board, pv)
        if line:
            return line
    gain, length = forcing_win(board, pv, just_lost)
    if gain >= MIN_GAIN_PAWNS and length:
        return pv[:length]
    return pv[:1]


def classify(engine: chess.engine.SimpleEngine, board: chess.Board, just_lost: int = 0) -> Verdict:
    """Tactic = forced mate (MAX_MATE moves or fewer) or a forcing sequence
    that wins material. Quiet = neither, and the second-best move is within
    MOVE_OK_CP of the best. Anything else (sharp but not tactical, e.g. an
    only-move defence) is "unclear" and isn't used for a round.

    just_lost: material the opponent's last move captured from you. A win
    has to beat that — taking a piece back after they took yours is just
    finishing an exchange, not a tactic."""
    me = board.turn
    lines = engine.analyse(board, chess.engine.Limit(depth=CLASSIFY_DEPTH), multipv=2)
    top = lines[0]
    best = top["pv"][0]
    best_cp = _score_to_cp(top["score"], me)
    second_cp = _score_to_cp(lines[1]["score"], me) if len(lines) > 1 and lines[1].get("pv") else None
    pv = list(top.get("pv", []))
    pov = top["score"].pov(me)
    if pov.is_mate() and 0 < pov.mate() <= MAX_MATE:
        return Verdict(True, "mate", best, best_cp, second_cp, pv, mate_in=pov.mate(),
                       solution=mate_line(board, pv) or [best])
    gain, length = forcing_win(board, pv, just_lost)
    if gain >= MIN_GAIN_PAWNS and best_cp >= MIN_WINNING_CP:
        return Verdict(True, "material", best, best_cp, second_cp, pv, gain=gain, solution=pv[:length])
    if second_cp is not None and best_cp - second_cp < MOVE_OK_CP and not pov.is_mate():
        return Verdict(False, "quiet", best, best_cp, second_cp, pv, solution=[best])
    return Verdict(False, "unclear", best, best_cp, second_cp, pv, solution=[best])


def judge_move(engine: chess.engine.SimpleEngine, board: chess.Board, verdict: Verdict,
               move: chess.Move) -> Tuple[bool, int]:
    """(good?, how many centipawns worse than the best move). Tactic
    positions: only the best move (or an immediate mate) is good, however
    close another move scores. Quiet positions: within MOVE_OK_CP."""
    if move == verdict.best:
        return True, 0
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return True, 0
    info = engine.analyse(after, chess.engine.Limit(depth=JUDGE_DEPTH))
    cp = _score_to_cp(info["score"], board.turn)
    loss = max(0, verdict.best_cp - cp)
    if verdict.best_cp >= MATE_CP and cp >= MATE_CP:   # still mating, just slower
        loss = 0
    if verdict.is_tactic:
        return False, loss
    return loss <= MOVE_OK_CP, loss


def correct_line(engine: chess.engine.SimpleEngine, board: chess.Board, verdict: Verdict) -> List[chess.Move]:
    """Best move plus Stockfish's continuation, for stepping through."""
    after = board.copy(stack=False)
    after.push(verdict.best)
    if after.is_game_over():
        return [verdict.best]
    info = engine.analyse(after, chess.engine.Limit(depth=LINE_DEPTH))
    return [verdict.best] + list(info.get("pv", []))[:LINE_PLIES]


def line_evals(engine: chess.engine.SimpleEngine, board: chess.Board, line: List[chess.Move]) -> List[int]:
    """Evaluation (White's point of view) of every position along the line:
    index i = after the first i moves — for the evaluation bar."""
    evals, b = [], board.copy(stack=False)
    for i in range(len(line) + 1):
        if b.is_checkmate():
            evals.append(-MATE_CP if b.turn == chess.WHITE else MATE_CP)
        elif b.is_game_over():
            evals.append(0)
        else:
            info = engine.analyse(b, chess.engine.Limit(depth=EVAL_DEPTH))
            evals.append(_score_to_cp(info["score"], chess.WHITE))
        if i < len(line):
            b.push(line[i])
    return evals


# ---------- results ----------

def record(conn: sqlite3.Connection, fen: str, verdict: Verdict, said_tactic: bool, move_ok: bool) -> None:
    conn.execute(
        "INSERT INTO spot_attempts (fen, is_tactic, reason, said_tactic, move_ok, attempted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fen, int(verdict.is_tactic), verdict.reason, int(said_tactic), int(move_ok),
         dt.datetime.now().isoformat()),
    )
    conn.commit()


def xp(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(is_tactic = said_tactic), 0), COALESCE(SUM(move_ok), 0) FROM spot_attempts"
    ).fetchone()
    return row[0] * CALL_XP + row[1] * MOVE_XP


@dataclass
class Summary:
    rounds: int = 0
    tactics: int = 0
    spotted: int = 0              # tactic positions you said yes to
    quiet: int = 0
    false_alarms: int = 0         # quiet positions you said yes to
    moves_ok: int = 0


def summary(conn: sqlite3.Connection) -> Summary:
    s = Summary()
    for r in conn.execute("SELECT is_tactic, said_tactic, move_ok FROM spot_attempts"):
        s.rounds += 1
        s.moves_ok += r[2]
        if r[0]:
            s.tactics += 1
            s.spotted += r[1]
        else:
            s.quiet += 1
            s.false_alarms += r[1]
    return s


def seen_fens(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT fen FROM spot_attempts")}


def usable_as_quiet(cand: Candidate) -> bool:
    """A quiet round needs at least 5 moves played by each side."""
    return cand.ply >= MIN_QUIET_PLY


def want_quiet() -> bool:
    return random.random() < QUIET_SHARE
