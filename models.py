"""
Core data models for the chess prep app.
These mirror the SwiftData schema (Game, Puzzle, OpeningLine, ReviewState)
so the logic ports cleanly to Swift later.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlayerColor(str, Enum):
    WHITE = "white"
    BLACK = "black"


class GameResult(str, Enum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


class PuzzleSource(str, Enum):
    OWN_GAME_MISTAKE = "own_game_mistake"
    OPENING_PREP = "opening_prep"


def new_id() -> str:
    return str(uuid.uuid4())


def time_class(time_control: Optional[str]) -> str:
    """chess.com's buckets from a time-control string like '180+2' or '1/86400'."""
    if not time_control or "/" in time_control:
        return "Daily"
    try:
        base, _, inc = time_control.partition("+")
        estimate = int(base) + 40 * int(inc or 0)
    except ValueError:
        return "Other"
    if estimate < 180:
        return "Bullet"
    if estimate < 600:
        return "Blitz"
    return "Rapid"


# dropdown label -> time classes included (None = every game); shared by
# the Stats tab and Puzzle Review's focus filter
TIME_FILTERS = {
    "All games": None,
    "All except Bullet": {"Blitz", "Rapid", "Daily", "Other"},
    "Rapid": {"Rapid"},
    "Blitz": {"Blitz"},
    "Bullet": {"Bullet"},
    "Daily": {"Daily"},
}


def in_time_filter(time_control: Optional[str], time_classes: Optional[set]) -> bool:
    return time_classes is None or time_class(time_control) in time_classes


@dataclass
class Game:
    chess_com_url: str
    pgn: str
    played_at: dt.datetime
    time_control: str
    my_color: PlayerColor
    opponent_username: str
    result: GameResult
    id: str = field(default_factory=new_id)
    is_analyzed: bool = False
    analyzed_at: Optional[dt.datetime] = None


@dataclass
class Puzzle:
    fen: str                       # position BEFORE the move to be tested
    correct_move_san: str
    correct_move_uci: str
    source: PuzzleSource
    id: str = field(default_factory=new_id)
    played_move_san: Optional[str] = None      # set if source == OWN_GAME_MISTAKE
    eval_swing_cp: Optional[int] = None        # magnitude of the mistake
    eval_before_cp: Optional[int] = None       # eval if the best move is played
    eval_after_cp: Optional[int] = None        # eval after the played (mistake) move
    puzzle_type: Optional[str] = None          # heuristic tag, e.g. "Hanging Piece"
    source_game_id: Optional[str] = None
    opening_line_id: Optional[str] = None
    created_at: dt.datetime = field(default_factory=dt.datetime.now)


@dataclass
class MoveEvaluation:
    """Raw Stockfish output for one of YOUR moves in a game, cached
    permanently so a game never has to go through the engine twice.
    No blunder threshold is applied here — puzzles are derived from these
    later, at whatever threshold is currently set."""
    game_id: str
    ply: int                          # 0-based half-move index within the game
    fen_before: str                   # position you moved from
    move_uci: str                     # what you played
    best_move_uci: Optional[str]      # engine's top choice (None if it gave no PV)
    cp_before: int                    # eval before your move, from your POV
    cp_after: Optional[int]           # eval after your move; None when the position was
                                      # already decisive and the second engine call was skipped
    depth: int
    # engine's 2nd/3rd choices (MultiPV) from the same position, your POV.
    # A big gap between best and 2nd best = an "only move" (sharp) position;
    # three moves all close together = a quiet one. None for evaluations
    # cached before MultiPV was added, or when fewer legal moves existed.
    second_move_uci: Optional[str] = None
    cp_second: Optional[int] = None
    third_move_uci: Optional[str] = None
    cp_third: Optional[int] = None


@dataclass
class OpeningLine:
    """One ply in a repertoire tree. Root nodes have parent_id = None."""
    move_san: str
    move_uci: str
    fen_after_move: str
    repertoire_name: str
    my_color: PlayerColor
    move_number: int
    id: str = field(default_factory=new_id)
    parent_id: Optional[str] = None
    is_line_end: bool = False
    line_name: Optional[str] = None    # e.g. "Orthodox Defense" — from the PGN's [Event] tag
    is_enabled: bool = True            # toggled off to exclude from drilling without deleting
    fen_before_move: Optional[str] = None  # position this move was played from — lets book-move
                                            # matching find equivalent positions across SEPARATELY
                                            # imported lines, not just literal tree siblings
    notes: Optional[str] = None


@dataclass
class ReviewState:
    """SM-2 style spaced repetition state, one per puzzle."""
    puzzle_id: str
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_date: dt.datetime = field(default_factory=dt.datetime.now)
    last_reviewed_at: Optional[dt.datetime] = None
    consecutive_correct: int = 0
    consecutive_wrong: int = 0

    def record_attempt(self, quality: int) -> None:
        """quality: 0-5 (SM-2 scale). 5 = instant correct, 3 = correct but
        hesitant, 0 = wrong."""
        self.last_reviewed_at = dt.datetime.now()

        if quality >= 3:
            self.consecutive_correct += 1
            self.consecutive_wrong = 0
            if self.repetitions == 0:
                self.interval_days = 1
            elif self.repetitions == 1:
                self.interval_days = 6
            else:
                self.interval_days = round(self.interval_days * self.ease_factor)
            self.repetitions += 1
        else:
            self.consecutive_wrong += 1
            self.consecutive_correct = 0
            self.repetitions = 0
            self.interval_days = 1

        q = quality
        self.ease_factor = max(
            1.3,
            self.ease_factor + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)),
        )
        self.due_date = dt.datetime.now() + dt.timedelta(days=self.interval_days)
