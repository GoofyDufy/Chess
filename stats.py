"""
Weakness statistics over your imported games. Pure functions, no UI.

Sources:
  - PGN headers chess.com includes (ECOUrl, Termination) — every game
  - [%clk] comments in the PGN — clock left after each move
  - move_evaluations (the Stockfish cache) — only games analyzed since
    the cache existed, so phase/clock stats grow as more games are analyzed
"""

from __future__ import annotations

import io
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import chess
import chess.pgn

from models import time_class

BLUNDER_CP = 300
# cap per-move loss so a single mate-clamp (+/-10000) doesn't swamp averages
MAX_LOSS_CP = 1000
OPENING_MAX_MOVE = 12
# non-pawn, non-king material (both sides, Q=9 R=5 B/N=3) at or below this = endgame
ENDGAME_MATERIAL = 13

_CLK_RE = re.compile(r"%clk (\d+):(\d+):(\d+(?:\.\d+)?)")
_PIECE_POINTS = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3}

CLOCK_BUCKETS = [(10, "< 10 s"), (30, "10–30 s"), (60, "30–60 s"),
                 (180, "1–3 min"), (float("inf"), "> 3 min")]


@dataclass
class Record:
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def score(self) -> float:
        """Chess score: win = 1, draw = 0.5, as a fraction of games."""
        return (self.wins + 0.5 * self.draws) / self.games if self.games else 0.0

    def add(self, result: str) -> None:
        if result == "win":
            self.wins += 1
        elif result == "loss":
            self.losses += 1
        else:
            self.draws += 1


@dataclass
class PhaseStat:
    moves: int = 0
    total_loss: int = 0
    blunders: int = 0

    @property
    def avg_loss(self) -> float:
        return self.total_loss / self.moves if self.moves else 0.0

    @property
    def blunder_rate(self) -> float:
        return self.blunders / self.moves if self.moves else 0.0


@dataclass
class Stats:
    total_games: int = 0
    engine_games: int = 0
    # (opening name, color) -> record
    openings: Dict[tuple, Record] = field(default_factory=dict)
    # time class -> reason -> count (losses only)
    loss_reasons: Dict[str, Dict[str, int]] = field(default_factory=dict)
    phases: Dict[str, PhaseStat] = field(default_factory=dict)
    clock: Dict[str, PhaseStat] = field(default_factory=dict)


def opening_name(headers: chess.pgn.Headers) -> str:
    """Short opening family from chess.com's ECOUrl, e.g.
    '.../Queens-Gambit-Declined-Queens-Knight-Variation-3...Nf6' ->
    'Queens Gambit Declined Queens'. Cut at the first move-sequence token
    and at 4 words, so games group into families instead of exact lines."""
    url = headers.get("ECOUrl", "")
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    words = []
    for w in slug.split("-"):
        if not w or any(c.isdigit() for c in w):
            break
        words.append(w)
    return " ".join(words[:4]) or headers.get("ECO", "Unknown")


def loss_reason(termination: str) -> str:
    t = termination.lower()
    if "checkmate" in t:
        return "Checkmated"
    if "resignation" in t:
        return "Resigned"
    if "on time" in t:
        return "Timeout"
    if "abandon" in t:
        return "Abandoned"
    return "Other"


def _clocks(pgn: str) -> List[float]:
    """Seconds left after each ply, in ply order (empty if no clock data)."""
    return [int(h) * 3600 + int(m) * 60 + float(s) for h, m, s in _CLK_RE.findall(pgn)]


def _phase(ply: int, fen: str) -> str:
    if ply // 2 + 1 <= OPENING_MAX_MOVE:
        return "Opening"
    board = chess.Board(fen)
    material = sum(
        _PIECE_POINTS.get(p.piece_type, 0) for p in board.piece_map().values()
    )
    return "Endgame" if material <= ENDGAME_MATERIAL else "Middlegame"


def is_endgame(fen: str) -> bool:
    """Little non-pawn material left (see ENDGAME_MATERIAL)."""
    board = chess.Board(fen)
    return sum(_PIECE_POINTS.get(p.piece_type, 0) for p in board.piece_map().values()) <= ENDGAME_MATERIAL


def _clock_bucket(seconds: float) -> str:
    for limit, label in CLOCK_BUCKETS:
        if seconds < limit:
            return label
    return CLOCK_BUCKETS[-1][1]


def compute(conn: sqlite3.Connection, time_classes: Optional[Set[str]] = None) -> Stats:
    """time_classes: only include games in these classes (e.g. {'Blitz',
    'Rapid'}); None = every game."""
    stats = Stats(
        phases={p: PhaseStat() for p in ("Opening", "Middlegame", "Endgame")},
        clock={label: PhaseStat() for _, label in CLOCK_BUCKETS},
    )
    openings: Dict[tuple, Record] = defaultdict(Record)
    loss_reasons: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    clocks_by_game: Dict[str, List[float]] = {}

    for g in conn.execute("SELECT id, pgn, my_color, result, time_control FROM games"):
        tc = time_class(g["time_control"])
        if time_classes is not None and tc not in time_classes:
            continue
        stats.total_games += 1
        headers = chess.pgn.read_headers(io.StringIO(g["pgn"])) or chess.pgn.Headers()
        openings[(opening_name(headers), g["my_color"])].add(g["result"])
        if g["result"] == "loss":
            loss_reasons[tc][loss_reason(headers.get("Termination", ""))] += 1
        clocks_by_game[g["id"]] = _clocks(g["pgn"])

    engine_games = set()
    for ev in conn.execute(
        "SELECT game_id, ply, fen_before, cp_before, cp_after FROM move_evaluations"
    ):
        if ev["cp_after"] is None:
            continue  # decisive position, second engine call skipped
        if ev["game_id"] not in clocks_by_game:
            continue  # game filtered out by time class
        engine_games.add(ev["game_id"])
        loss = min(max(0, ev["cp_before"] - ev["cp_after"]), MAX_LOSS_CP)
        blunder = loss >= BLUNDER_CP

        targets = [stats.phases[_phase(ev["ply"], ev["fen_before"])]]
        clocks = clocks_by_game.get(ev["game_id"], [])
        if ev["ply"] < len(clocks):
            targets.append(stats.clock[_clock_bucket(clocks[ev["ply"]])])
        for t in targets:
            t.moves += 1
            t.total_loss += loss
            t.blunders += blunder

    stats.engine_games = len(engine_games)
    stats.openings = dict(openings)
    stats.loss_reasons = {k: dict(v) for k, v in loss_reasons.items()}
    return stats
