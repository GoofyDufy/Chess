"""
Imports a PGN repertoire file into the OpeningLine tree.

Supports branching lines: if your PGN uses variations (sidelines in
parentheses, as exported by lichess studies / most opening-trainer tools),
each variation becomes a separate branch in the tree. You can also just
paste several separate single-line PGNs in one file — each game becomes
its own root-to-leaf chain.

Usage:
    import_pgn_text(pgn_text, repertoire_name="White: 1.e4", my_color=PlayerColor.WHITE, conn=conn)
"""

from __future__ import annotations

import io
import sqlite3

import chess
import chess.pgn

import db
from models import OpeningLine, PlayerColor


def _walk(node: chess.pgn.GameNode, parent_id: str | None,
          repertoire_name: str, my_color: PlayerColor, line_name: str,
          conn: sqlite3.Connection) -> None:
    for variation in node.variations:
        move = variation.move
        board_before = node.board()
        san = board_before.san(move)

        board_after = variation.board()
        fen_after = board_after.fen()
        move_number = board_before.fullmove_number * 2 - (2 if board_before.turn == chess.WHITE else 1)

        line = OpeningLine(
            move_san=san,
            move_uci=move.uci(),
            fen_after_move=fen_after,
            repertoire_name=repertoire_name,
            my_color=my_color,
            move_number=move_number,
            parent_id=parent_id,
            is_line_end=(len(variation.variations) == 0),
            line_name=line_name,
            fen_before_move=board_before.fen(),
        )
        db.save_opening_line(conn, line)

        # recurse into this variation's own continuations/sidelines
        _walk(variation, line.id, repertoire_name, my_color, line_name, conn)


def import_pgn_text(pgn_text: str, repertoire_name: str,
                     my_color: PlayerColor, conn: sqlite3.Connection) -> int:
    """Parses all games in pgn_text and inserts them as trees.
    Returns the number of positions (nodes) imported. Each game's PGN
    [Event] header (if present) is stored as that line's display name —
    e.g. "Queens Gambit - Orthodox Defense" — falling back to the
    repertoire name if no Event header is set."""
    pgn_io = io.StringIO(pgn_text)
    count_before = len(conn.execute(
        "SELECT id FROM opening_lines WHERE repertoire_name = ? AND my_color = ?",
        (repertoire_name, my_color.value),
    ).fetchall())

    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            break
        line_name = game.headers.get("Event", repertoire_name)
        if line_name == "?":  # python-chess's default when no header is set
            line_name = repertoire_name
        _walk(game, None, repertoire_name, my_color, line_name, conn)

    count_after = len(conn.execute(
        "SELECT id FROM opening_lines WHERE repertoire_name = ? AND my_color = ?",
        (repertoire_name, my_color.value),
    ).fetchall())
    return count_after - count_before


def import_pgn_file(path: str, repertoire_name: str,
                     my_color: PlayerColor, conn: sqlite3.Connection) -> int:
    with open(path, "r", encoding="utf-8") as f:
        return import_pgn_text(f.read(), repertoire_name, my_color, conn)
