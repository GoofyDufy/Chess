"""
Playing-style analysis: are you relatively stronger in sharp/tactical or
quiet/positional positions, and which openings fit that. Pure functions,
no UI, no engine — works off the cached move_evaluations.

Like the puzzle-type classifier, every label here is a hand-built
heuristic, good for trends over many games, not verdicts on single moves:

  - A position is SHARP when the engine's best move is clearly better
    than its 2nd choice (an "only move" — MultiPV data), or, for games
    cached before MultiPV, when you're in check or a piece is hanging.
  - A game's sharpness = share of your moves 9-30 played in sharp positions.
  - The verdict compares your SCORE in your sharpest vs quietest games
    (split at your own 1/3 and 2/3 percentiles). Raw per-move accuracy
    isn't used for the verdict: sharp positions are harder for everyone,
    so you'd always look "worse tactically" without a peer baseline.
"""

from __future__ import annotations

import io
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import chess
import chess.pgn

from analyzer import MATE_CP
from models import in_time_filter
from stats import BLUNDER_CP, MAX_LOSS_CP, PhaseStat, Record, opening_name

ONLY_MOVE_GAP_CP = 120        # best beats 2nd best by this much = only move
PUNISH_CHANCE_CP = 200        # opponent's move handed you at least this much
PUNISH_KEEP_CP = 100          # "punished" = you gave back less than this
# below this, a sharp-vs-quiet score gap is mostly noise (~±10%)
MIN_GAMES_PER_BUCKET = 25
VERDICT_MARGIN = 0.06         # score difference needed to call a preference
_HANG_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _clamp(cp: int) -> int:
    return max(-MATE_CP, min(MATE_CP, cp))


def _has_hanging_piece(board: chess.Board) -> bool:
    for square, piece in board.piece_map().items():
        if piece.piece_type in _HANG_VALUES:
            if board.attackers(not piece.color, square) and not board.attackers(piece.color, square):
                return True
    return False


def is_sharp(row: sqlite3.Row) -> bool:
    if row["cp_second"] is not None:
        return _clamp(row["cp_before"]) - _clamp(row["cp_second"]) >= ONLY_MOVE_GAP_CP
    board = chess.Board(row["fen_before"])
    return board.is_check() or _has_hanging_piece(board)


def pawn_structure(fen: str) -> str:
    """Closed = 3+ pawn pairs locked head-to-head; Open = at most one
    locked pair and 12 or fewer pawns left; otherwise Semi-open."""
    board = chess.Board(fen)
    white_pawns = board.pieces(chess.PAWN, chess.WHITE)
    black_pawns = board.pieces(chess.PAWN, chess.BLACK)
    locked = sum(1 for sq in white_pawns if sq + 8 < 64 and (sq + 8) in black_pawns)
    if locked >= 3:
        return "Closed center"
    if locked <= 1 and len(white_pawns) + len(black_pawns) <= 12:
        return "Open center"
    return "Semi-open center"


@dataclass
class StyleReport:
    games: int = 0                  # games with engine data in the filter
    multipv_games: int = 0
    accuracy: Dict[str, PhaseStat] = field(default_factory=dict)   # Sharp / Quiet positions
    only_moves: int = 0
    only_moves_found: int = 0
    punish_chances: int = 0
    punished: int = 0
    game_types: Dict[str, Record] = field(default_factory=dict)    # ordered for display
    # (opening, color) -> (record, average game sharpness)
    openings: Dict[Tuple[str, str], Tuple[Record, float]] = field(default_factory=dict)
    sharp_cutoffs: Tuple[float, float] = (0.0, 0.0)
    style: Optional[str] = None     # "sharp" / "quiet" / "balanced" / None = not enough data
    verdict: str = ""

    def character(self, sharpness: float) -> str:
        low, high = self.sharp_cutoffs
        return "Sharp" if sharpness >= high else "Quiet" if sharpness <= low else "Mixed"


def compute_style(conn: sqlite3.Connection, time_classes: Optional[Set[str]] = None) -> StyleReport:
    report = StyleReport(accuracy={"Sharp positions": PhaseStat(), "Quiet positions": PhaseStat()})

    games = {}
    for g in conn.execute(
        "SELECT id, pgn, my_color, result, time_control FROM games WHERE evaluated_at IS NOT NULL"
    ):
        if in_time_filter(g["time_control"], time_classes):
            games[g["id"]] = g

    evals_by_game: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for ev in conn.execute("SELECT * FROM move_evaluations ORDER BY game_id, ply"):
        if ev["game_id"] in games:
            evals_by_game[ev["game_id"]].append(ev)

    game_sharpness: Dict[str, float] = {}
    structure_of: Dict[str, str] = {}
    queens_of: Dict[str, str] = {}

    for game_id, evs in evals_by_game.items():
        if any(e["cp_second"] is not None for e in evs):
            report.multipv_games += 1
        sharp_mid = total_mid = 0
        prev = None
        for ev in evs:
            sharp = is_sharp(ev)
            if 16 <= ev["ply"] <= 60:
                total_mid += 1
                sharp_mid += sharp

            if ev["cp_after"] is not None:
                loss = min(max(0, ev["cp_before"] - ev["cp_after"]), MAX_LOSS_CP)
                stat = report.accuracy["Sharp positions" if sharp else "Quiet positions"]
                stat.moves += 1
                stat.total_loss += loss
                stat.blunders += loss >= BLUNDER_CP

            # only-move positions (MultiPV data only): did you find it?
            if ev["cp_second"] is not None and sharp:
                report.only_moves += 1
                report.only_moves_found += ev["move_uci"] == ev["best_move_uci"]

            # opponent's move in between handed you a big gain: did you cash in?
            if prev is not None and prev["ply"] + 2 == ev["ply"] and prev["cp_after"] is not None:
                gain = min(ev["cp_before"], MAX_LOSS_CP) - max(prev["cp_after"], -MAX_LOSS_CP)
                if gain >= PUNISH_CHANCE_CP and abs(prev["cp_after"]) < MATE_CP:
                    report.punish_chances += 1
                    if ev["cp_after"] is not None:
                        report.punished += ev["cp_before"] - ev["cp_after"] < PUNISH_KEEP_CP
                    else:
                        report.punished += ev["move_uci"] == ev["best_move_uci"]
            prev = ev

            if game_id not in structure_of and ev["ply"] >= 28:
                structure_of[game_id] = pawn_structure(ev["fen_before"])
            if game_id not in queens_of and ev["ply"] >= 40:
                board = chess.Board(ev["fen_before"])
                queens_of[game_id] = ("Queens kept past move 20" if board.pieces(chess.QUEEN, chess.WHITE)
                                      or board.pieces(chess.QUEEN, chess.BLACK) else "Queens off by move 20")
        if total_mid >= 5:
            game_sharpness[game_id] = sharp_mid / total_mid

    report.games = len(evals_by_game)
    if len(game_sharpness) >= 3:
        ordered = sorted(game_sharpness.values())
        report.sharp_cutoffs = (ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3])

    types = {k: Record() for k in (
        "Sharp games", "Mixed games", "Quiet games",
        "Open center", "Semi-open center", "Closed center",
        "Queens off by move 20", "Queens kept past move 20",
    )}
    opening_records: Dict[Tuple[str, str], Record] = defaultdict(Record)
    opening_sharpness: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for game_id, g in games.items():
        if game_id in game_sharpness:
            types[f"{report.character(game_sharpness[game_id])} games"].add(g["result"])
        if game_id in structure_of:
            types[structure_of[game_id]].add(g["result"])
        if game_id in queens_of:
            types[queens_of[game_id]].add(g["result"])
        headers = chess.pgn.read_headers(io.StringIO(g["pgn"])) or chess.pgn.Headers()
        key = (opening_name(headers), g["my_color"])
        opening_records[key].add(g["result"])
        if game_id in game_sharpness:
            opening_sharpness[key].append(game_sharpness[game_id])
    report.game_types = types
    report.openings = {
        k: (r, sum(opening_sharpness[k]) / len(opening_sharpness[k]))
        for k, r in opening_records.items() if opening_sharpness[k]
    }

    sharp, quiet = types["Sharp games"], types["Quiet games"]
    if min(sharp.games, quiet.games) < MIN_GAMES_PER_BUCKET:
        report.verdict = (f"Not enough data yet — need {MIN_GAMES_PER_BUCKET}+ sharp and quiet "
                          f"games each (have {sharp.games} / {quiet.games}). Analyze more games.")
    else:
        diff = sharp.score - quiet.score
        detail = f"You score {sharp.score:.0%} in your sharpest games vs {quiet.score:.0%} in your quietest."
        if diff >= VERDICT_MARGIN:
            report.style = "sharp"
            report.verdict = f"You're relatively stronger in sharp, tactical games. {detail}"
        elif diff <= -VERDICT_MARGIN:
            report.style = "quiet"
            report.verdict = f"You're relatively stronger in quiet, positional games. {detail}"
        else:
            report.style = "balanced"
            report.verdict = f"No clear preference — you're a balanced player. {detail}"
    return report
