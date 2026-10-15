"""One Stockfish for the whole app's interactive work: the analysis boards,
Spot the Tactic, What's the threat?, Sacrifice!!, puzzle explanations, the
opening commentary and the library's descriptions. Started the first time
it's needed and kept running, so opening an analysis board doesn't wait
for Stockfish to start, and the screens don't run several copies at once.

Background threads only (never the UI thread: a search can take seconds):

    with engine_service.borrow() as engine:
        engine.analyse(...)

Stockfish works on one search at a time (a new command would cancel the
one running), so each analyse() takes a lock for just that search: a long
job on one screen (preparing the next Sacrifice!! round: many searches)
lets an analysis board's searches in between.

Bulk work keeps its own engines: game analysis (analyzer.py, several in
parallel), puzzle sorting (puzzle_kind.py) and Play (play_window.py, which
weakens its engine).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import chess.engine

import engine_locator

_lock = threading.RLock()
_engine: Optional[chess.engine.SimpleEngine] = None
_closed = False                # after shutdown(): no restarting (a late job would keep the app alive)


class NoStockfish(RuntimeError):
    pass


def available() -> bool:
    return _engine is not None or engine_locator.find_stockfish() is not None


class _Shared:
    """Looks like a SimpleEngine to the code using it (only analyse() is used)."""

    def analyse(self, board, limit, **kwargs):
        with _lock:
            engine = _start()
            try:
                return engine.analyse(board, limit, **kwargs)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
                _quit()                       # restarted on the next search
                raise


_SHARED = _Shared()


@contextmanager
def borrow() -> Iterator[_Shared]:
    """The shared engine for a job. Raises NoStockfish if there's none."""
    if not available():
        raise NoStockfish("Stockfish not found — see Import & Analyze for setup help.")
    yield _SHARED


def _start() -> chess.engine.SimpleEngine:
    global _engine
    if _closed:
        raise NoStockfish("The app is closing.")
    if _engine is None:
        path = engine_locator.find_stockfish()
        if path is None:
            raise NoStockfish("Stockfish not found — see Import & Analyze for setup help.")
        _engine = chess.engine.SimpleEngine.popen_uci(path)
    return _engine


def _quit() -> None:
    global _engine
    engine, _engine = _engine, None
    if engine is not None:
        try:
            engine.quit()
        except Exception:
            try:
                engine.close()
            except Exception:
                pass


def shutdown() -> None:
    """At exit: python-chess's engine thread would otherwise keep the app alive.
    A search running is cut short (its caller gets an error, harmlessly)."""
    global _closed
    _closed = True
    acquired = _lock.acquire(timeout=0.5)
    try:
        _quit()
    finally:
        if acquired:
            _lock.release()


def reopen() -> None:
    """A new app window (after shutdown(), e.g. in tests): Stockfish may start again."""
    global _closed
    _closed = False
