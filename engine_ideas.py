"""Stockfish's ideas for a position, for studying lines (the Tree view in
Opening Drill): its top moves with their evaluations and lines, a quick
answer first and a deeper one after.

The app's shared Stockfish (engine_service.py), from a background thread:
analyse() queues a position — only the newest one is worked on, so clicking
through a line never builds a backlog — and results come back to the UI
thread through an after() poller as on_result(fen, depth, lines).
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

import chess
import chess.engine

import engine_service
from analyzer import MATE_CP

LINES = 3                  # top moves shown
QUICK_DEPTH = 12           # first answer (well under a second)
DEEP_DEPTH = 18            # then this
PV_PLIES = 6               # moves of each line shown (keeps each on one line under the board)
POLL_MS = 100


@dataclass
class Idea:
    move: chess.Move
    pv: List[chess.Move]
    cp_white: int           # evaluation from White's point of view (mate = +/-MATE_CP)
    mate: Optional[int]     # moves to mate (positive: White mates), None if none

    def eval_text(self) -> str:
        if self.mate is not None:
            return f"#{self.mate}" if self.mate > 0 else f"#-{-self.mate}"
        return f"{self.cp_white / 100:+.2f}"


def line_text(board: chess.Board, pv: List[chess.Move]) -> str:
    """'7.Qc2 c6 8.Nbd2' (or '7...c6 8.Nbd2') from this position."""
    b, parts = board.copy(stack=False), []
    for i, move in enumerate(pv):
        if move not in b.legal_moves:
            break
        if b.turn == chess.WHITE:
            parts.append(f"{b.fullmove_number}.{b.san(move)}")
        else:
            parts.append(f"{b.fullmove_number}...{b.san(move)}" if i == 0 else b.san(move))
        b.push(move)
    return " ".join(parts)


class IdeasEngine:
    def __init__(self, widget, on_result: Callable[[str, int, List[Idea]], None],
                 on_error: Optional[Callable[[str], None]] = None):
        self.widget = widget
        self.on_result = on_result
        self.on_error = on_error
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._started = False
        self._stopped = False

    def analyse(self, fen: str) -> None:
        if not self._started:
            if not engine_service.available():
                if self.on_error:
                    self.on_error("Stockfish not found — see Import & Analyze for setup help.")
                return
            self._started = True
            threading.Thread(target=self._loop, daemon=True).start()
            self.widget.after(POLL_MS, self._poll)
        self._jobs.put(fen)

    def stop(self) -> None:
        self._stopped = True
        self._jobs.put(None)

    def _newest_job(self, block: bool):
        job = self._jobs.get() if block else self._jobs.get_nowait()
        while True:                      # only the newest position matters
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return job

    def _loop(self) -> None:
        while True:
            fen = self._newest_job(block=True)
            if fen is None:
                break
            board = chess.Board(fen)
            if board.is_game_over():
                self._results.put(("ideas", fen, 0, []))
                continue
            try:
                for depth in (QUICK_DEPTH, DEEP_DEPTH):
                    if not self._jobs.empty():
                        break            # moved on already
                    with engine_service.borrow() as engine:
                        infos = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=LINES)
                    self._results.put(("ideas", fen, depth, [self._idea(i) for i in infos if i.get("pv")]))
            except Exception as e:
                self._results.put(("error", str(e)))

    @staticmethod
    def _idea(info) -> Idea:
        score = info["score"].white()
        pv = list(info["pv"])[:PV_PLIES]
        mate = score.mate() if score.is_mate() else None
        return Idea(pv[0], pv, score.score(mate_score=MATE_CP), mate)

    def _poll(self) -> None:
        if self._stopped:
            return
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return
        try:
            while True:
                item = self._results.get_nowait()
                if item[0] == "ideas":
                    self.on_result(item[1], item[2], item[3])
                elif self.on_error:
                    self.on_error(item[1])
        except queue.Empty:
            pass
        self.widget.after(POLL_MS, self._poll)
