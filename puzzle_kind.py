"""What kind of puzzle a position is, decided by Stockfish once and saved on
the puzzle (puzzles.solution_ucis / solution_moves / kind):

  - Attacking: the solution is a forced mate or a forcing sequence (checks,
    captures, new threats) that wins material — the rule Spot the Tactic and
    multi-move puzzles use (spot.solution_for / spot.forcing_win).
  - Defensive: you're facing a threat and the best move deals with it. Facing
    a threat = in check, or if you could pass, your opponent would gain at
    least THREAT_CP (Stockfish, a "null move").
  - Positional: neither — a quiet best move with no tactic to play and no
    threat to answer.

The solution (yours and the opponent's replies, up to 4 of your moves) is
saved too, so a puzzle opens without working it out again.
"""

from __future__ import annotations

import sqlite3
import threading
import queue
from dataclasses import dataclass
from typing import Callable, List, Optional

import chess
import chess.engine

import spot
from analyzer import _score_to_cp

ATTACKING, DEFENSIVE, POSITIONAL = "Attacking", "Defensive", "Positional"
KINDS = (ATTACKING, DEFENSIVE, POSITIONAL)
SOLUTION_DEPTH = 16        # same as the puzzle tab's on-the-fly line
THREAT_DEPTH = 12
THREAT_CP = 150


@dataclass
class Result:
    solution: List[chess.Move]
    kind: str

    @property
    def moves(self) -> int:
        return (len(self.solution) + 1) // 2


def classify(engine: chess.engine.SimpleEngine, fen: str, best_uci: str) -> Result:
    board = chess.Board(fen)
    best = chess.Move.from_uci(best_uci)
    info = engine.analyse(board, chess.engine.Limit(depth=SOLUTION_DEPTH))
    pv = list(info.get("pv", []))
    solution = [best]
    attacking = False
    if pv and pv[0] == best:                      # the engine agrees on the first move
        pov = info["score"].pov(board.turn)
        mate = pov.is_mate() and 0 < pov.mate() <= spot.MAX_MATE
        gain, length = spot.forcing_win(board, pv)
        if mate:
            solution = spot.mate_line(board, pv) or [best]
            attacking = True
        elif gain >= spot.MIN_GAIN_PAWNS and length:
            solution = pv[:length]
            attacking = True
    if attacking:
        return Result(solution, ATTACKING)
    if board.is_check():
        return Result(solution, DEFENSIVE)
    # a threat: what would the opponent get if you could pass?
    now = _score_to_cp(info["score"], board.turn)
    passed = board.copy(stack=False)
    passed.push(chess.Move.null())
    passed = chess.Board(passed.fen())          # the same position, without the pass in its history
    if passed.is_valid():
        after = engine.analyse(passed, chess.engine.Limit(depth=THREAT_DEPTH))
        if now - _score_to_cp(after["score"], board.turn) >= THREAT_CP:
            return Result(solution, DEFENSIVE)
    return Result(solution, POSITIONAL)


def save(conn: sqlite3.Connection, puzzle_id: str, result: Result) -> None:
    conn.execute("UPDATE puzzles SET solution_ucis = ?, solution_moves = ?, kind = ? WHERE id = ?",
                 (" ".join(m.uci() for m in result.solution), result.moves, result.kind, puzzle_id))
    conn.commit()


def unclassified(conn: sqlite3.Connection) -> List[tuple]:
    return [(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT id, fen, correct_move_uci FROM puzzles WHERE kind IS NULL")]


def classify_all(engine_path: str, todo: List[tuple], on_result: Callable[[str, Result], None],
                 engines: int = 2, stop: Optional[threading.Event] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None) -> int:
    """Classifies (id, fen, best) puzzles with `engines` Stockfish processes;
    on_result(id, result) and on_progress(done, total) are called under one
    lock (from worker threads — the caller saves). Returns how many finished."""
    jobs: "queue.Queue" = queue.Queue()
    for item in todo:
        jobs.put(item)
    lock = threading.Lock()
    done = [0]

    def work():
        with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
            while stop is None or not stop.is_set():
                try:
                    pid, fen, best = jobs.get_nowait()
                except queue.Empty:
                    return
                try:
                    result = classify(engine, fen, best)
                except (ValueError, chess.engine.EngineError):
                    continue
                with lock:
                    on_result(pid, result)
                    done[0] += 1
                    if on_progress:
                        on_progress(done[0], len(todo))

    workers = [threading.Thread(target=work, daemon=True) for _ in range(max(1, engines))]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return done[0]
