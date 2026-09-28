"""Opponent scouting as plain data: a player's most common opening lines
as White and as Black, their score, and whether YOUR repertoire (for the
other color) covers each line. Shared by the desktop Opponent Prep page
and the server API."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

import chess

from models import Game, in_time_filter
from repertoire_gaps import _opening_moves, _repertoire, format_line, position_key
from stats import Record

SCOUT_PLIES = 6
MIN_GAMES = 2


def scout_lines(conn: sqlite3.Connection, games: Iterable[Game],
                time_classes: Optional[Set[str]] = None, limit: int = 40) -> Dict[str, List[Dict]]:
    """games were fetched under the SCOUTED player's name, so Game.my_color
    and Game.result are from their point of view."""
    lines: Dict[Tuple[str, Tuple[str, ...]], Record] = defaultdict(Record)
    count = 0
    for g in games:
        if not in_time_filter(g.time_control, time_classes):
            continue
        count += 1
        board, clean = chess.Board(), []
        for san in _opening_moves(g.pgn, SCOUT_PLIES):
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            clean.append(board.san(move))
            board.push(move)
        if len(clean) >= SCOUT_PLIES:
            lines[(g.my_color.value, tuple(clean))].add(g.result.value)

    reps = {c: _repertoire(conn, c)[1] for c in ("white", "black")}
    out: Dict[str, List[Dict]] = {"games": count}
    for their_color in ("white", "black"):
        my_color = "black" if their_color == "white" else "white"
        my_turn = chess.WHITE if my_color == "white" else chess.BLACK
        rows = sorted(((k[1], r) for k, r in lines.items() if k[0] == their_color and r.games >= MIN_GAMES),
                      key=lambda kr: -kr[1].games)[:limit]
        result = []
        for sans, rec in rows:
            covered, board = True, chess.Board()
            for san in sans:
                move = board.parse_san(san)
                if board.turn == my_turn and (position_key(board.fen()), move.uci()) not in reps[my_color]:
                    covered = False
                board.push(move)
            result.append({"line": format_line(list(sans)), "sans": list(sans), "games": rec.games,
                           "wins": rec.wins, "draws": rec.draws, "losses": rec.losses,
                           "score": rec.score, "covered_by_your_repertoire": covered})
        out[their_color] = result
    return out
