"""
Which opening variations actually come up in your games, and where your
games leave your repertoire. Pure functions, no UI, no engine runs.

For every game, the first MAX_PLY half-moves are replayed against ALL your
enabled repertoire lines for the color you had (matched by position, so
transpositions count). The first position that isn't covered is a "gap":

  - opponent's move at a covered position isn't in your prep -> you need
    an answer to it (the actionable case: "Add to repertoire")
  - your own move isn't your prep -> you left your own repertoire
  - no repertoire at all for that color -> gap at move 1

Gaps are grouped by (position, move played) and sorted by how often they
happen. A suggested reply comes from the Stockfish cache (its best move in
that position, in any of your analyzed games) or, failing that, the move
you most often played there.
"""

from __future__ import annotations

import io
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import chess
import chess.pgn

from models import in_time_filter
from stats import Record, opening_name

MAX_PLY = 20            # only look at the first 10 moves
COMMON_PLIES = 6        # "common lines" are grouped by the first 3 moves each
MIN_GAMES = 2

_COMMENT_RE = re.compile(r"\{[^}]*\}|\([^)]*\)")
_MOVE_NUMBER_RE = re.compile(r"^\d+\.+$|^\d+\.{3}$")
_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


def position_key(fen: str) -> str:
    """FEN without the move counters, so transpositions match."""
    return " ".join(fen.split()[:4])


def _opening_moves(pgn: str, max_ply: int) -> List[str]:
    """First max_ply SAN tokens of a PGN, fast (no full game parse)."""
    body = pgn.split("\n\n", 1)[-1]
    tokens = []
    for tok in _COMMENT_RE.sub(" ", body).split():
        tok = re.sub(r"^\d+\.+", "", tok)
        if not tok or tok in _RESULTS or tok.startswith("$"):
            continue
        tokens.append(tok)
        if len(tokens) >= max_ply:
            break
    return tokens


def format_line(sans: List[str], start_ply: int = 0) -> str:
    parts = []
    for i, san in enumerate(sans, start=start_ply):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}. {san}")
        elif i == start_ply:
            parts.append(f"{i // 2 + 1}... {san}")
        else:
            parts.append(san)
    return " ".join(parts)


@dataclass
class Gap:
    color: str                   # your color in these games
    prefix: List[str]            # SAN moves up to the uncovered position
    move: str                    # the move played there
    by_opponent: bool
    covered: bool = True         # False = your repertoire has nothing at this position
    record: Record = field(default_factory=Record)
    opening: str = ""
    suggestion: Optional[str] = None       # your reply (SAN), opponent gaps only
    suggestion_source: str = ""            # "engine" / "your usual"
    game_ids: List[str] = field(default_factory=list)   # games this happened in

    @property
    def line(self) -> str:
        return format_line(self.prefix + [self.move])

    @property
    def review_fen(self) -> str:
        """The position where the game left your prep (before the gap move)."""
        board = chess.Board()
        for san in self.prefix:
            board.push_san(san)
        return board.fen()

    @property
    def who(self) -> str:
        if self.by_opponent:
            return "Opponent"
        return "You (left prep)" if self.covered else "You (no prep)"


@dataclass
class CommonLine:
    color: str
    sans: List[str]
    record: Record = field(default_factory=Record)
    opening: str = ""
    in_repertoire: bool = False
    game_ids: List[str] = field(default_factory=list)


@dataclass
class GapReport:
    games: int = 0
    gaps: List[Gap] = field(default_factory=list)
    common: List[CommonLine] = field(default_factory=list)
    has_repertoire: Dict[str, bool] = field(default_factory=dict)
    first_moves: Dict[str, int] = field(default_factory=dict)   # SAN of move 1 -> games


def _repertoire(conn: sqlite3.Connection, color: str) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    positions, moves = set(), set()
    for r in conn.execute(
        "SELECT fen_before_move, move_uci FROM opening_lines WHERE my_color = ? AND is_enabled = 1",
        (color,),
    ):
        key = position_key(r["fen_before_move"])
        positions.add(key)
        moves.add((key, r["move_uci"]))
    return positions, moves


def compute(conn: sqlite3.Connection, time_classes: Optional[Set[str]] = None) -> GapReport:
    report = GapReport()
    reps = {c: _repertoire(conn, c) for c in ("white", "black")}
    report.has_repertoire = {c: bool(reps[c][0]) for c in reps}

    gaps: Dict[Tuple[str, str, str], Gap] = {}
    gap_openings: Dict[Tuple[str, str, str], Counter] = defaultdict(Counter)
    common: Dict[Tuple[str, Tuple[str, ...]], CommonLine] = {}
    common_openings: Dict[Tuple[str, Tuple[str, ...]], Counter] = defaultdict(Counter)
    # my replies at positions right after an opponent gap: key -> Counter(uci)
    my_replies: Dict[str, Counter] = defaultdict(Counter)
    gap_after_keys: Dict[Tuple[str, str, str], str] = {}

    for g in conn.execute("SELECT id, pgn, my_color, result, time_control FROM games"):
        if not in_time_filter(g["time_control"], time_classes):
            continue
        report.games += 1
        color = g["my_color"]
        my_turn = chess.WHITE if color == "white" else chess.BLACK
        positions, rep_moves = reps[color]
        headers = chess.pgn.read_headers(io.StringIO(g["pgn"])) or chess.pgn.Headers()
        name = opening_name(headers)

        board = chess.Board()
        sans: List[str] = []
        gap_key = None
        for san in _opening_moves(g["pgn"], MAX_PLY):
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            key = position_key(board.fen())
            if gap_key is None and (key not in positions or (key, move.uci()) not in rep_moves):
                gap_key = (color, " ".join(sans), board.san(move))
                if gap_key not in gaps:
                    gaps[gap_key] = Gap(color, list(sans), board.san(move), board.turn != my_turn,
                                        covered=key in positions)
                gaps[gap_key].record.add(g["result"])
                gaps[gap_key].game_ids.append(g["id"])
                gap_openings[gap_key][name] += 1
            sans.append(board.san(move))
            board.push(move)
            if gap_key is not None and gap_key not in gap_after_keys and board.turn == my_turn:
                # position you had to answer right after the gap
                gap_after_keys[gap_key] = position_key(board.fen())
            if gap_key is not None and len(sans) == len(gaps[gap_key].prefix) + 2:
                # your reply to the opponent's out-of-book move
                my_replies[gap_after_keys.get(gap_key, "")][move.uci()] += 1
            if gap_key is not None and len(sans) >= max(COMMON_PLIES, len(gaps[gap_key].prefix) + 2):
                break   # past the gap and your reply to it; nothing more to learn

        if sans:
            report.first_moves[sans[0]] = report.first_moves.get(sans[0], 0) + 1
        if len(sans) >= COMMON_PLIES:
            ckey = (color, tuple(sans[:COMMON_PLIES]))
            if ckey not in common:
                replay = chess.Board()
                in_rep = True
                for s in sans[:COMMON_PLIES]:
                    mv = replay.parse_san(s)
                    if replay.turn == my_turn and (position_key(replay.fen()), mv.uci()) not in rep_moves:
                        in_rep = False
                    replay.push(mv)
                common[ckey] = CommonLine(color, list(ckey[1]), in_repertoire=in_rep)
            common[ckey].record.add(g["result"])
            common[ckey].game_ids.append(g["id"])
            common_openings[ckey][name] += 1

    # engine suggestions: best move Stockfish found in that exact position
    engine_best: Dict[str, Counter] = defaultdict(Counter)
    wanted = set(gap_after_keys.values())
    for r in conn.execute("SELECT fen_before, best_move_uci FROM move_evaluations WHERE best_move_uci IS NOT NULL"):
        key = position_key(r["fen_before"])
        if key in wanted:
            engine_best[key][r["best_move_uci"]] += 1

    for key, gap in gaps.items():
        gap.opening = gap_openings[key].most_common(1)[0][0]
        if not gap.by_opponent:
            continue
        after = gap_after_keys.get(key)
        if not after:
            continue
        board = chess.Board()
        for s in gap.prefix + [gap.move]:
            board.push_san(s)
        source = engine_best.get(after) or my_replies.get(after)
        if source:
            try:
                gap.suggestion = board.san(chess.Move.from_uci(source.most_common(1)[0][0]))
                gap.suggestion_source = "engine" if after in engine_best else "your usual"
            except (ValueError, AssertionError):
                pass

    for key, line in common.items():
        line.opening = common_openings[key].most_common(1)[0][0]

    report.gaps = sorted((g for g in gaps.values() if g.record.games >= MIN_GAMES),
                         key=lambda g: -g.record.games)
    report.common = sorted((c for c in common.values() if c.record.games >= MIN_GAMES),
                           key=lambda c: -c.record.games)
    return report


def gap_pgn(gap: Gap, line_name: str) -> Optional[str]:
    """A PGN for adding this gap to a repertoire: the opponent's move plus
    your suggested reply, or (your own move) the move you actually play."""
    if gap.by_opponent:
        if not gap.suggestion:
            return None
        sans = gap.prefix + [gap.move, gap.suggestion]
    else:
        sans = gap.prefix + [gap.move]
    moves = format_line(sans)
    return f'[Event "{line_name}"]\n\n{moves} *\n'
