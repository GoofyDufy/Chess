"""
Drives an opening drill.

Design: at the start of each round, one full line (root to leaf) is
selected at random from the repertoire tree and shown by name up front.
A move-list recap is available once you finish.

Book-move detection is informational only, not a substitute for the
target: when you miss, we separately check whether your move matches
some OTHER line in the repertoire at this position (matched by board
position, not database ancestry, since separately-imported PGN files
don't share parent/child rows even when they share moves) — this only
flags in DrillResult.in_book for messaging purposes. It never changes
which line is being drilled or accepts the move as correct. If you're
drilling a specific line, only that line's own next move counts.

Wrong moves don't auto-correct: attempt_my_move() leaves the board and
position untouched on a miss, so you can just try again, or call
reveal_and_advance() to see the answer.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

import chess

import db
from models import OpeningLine, PlayerColor


@dataclass
class DrillResult:
    correct: bool
    played_san: Optional[str]       # what you played (None if not a legal chess move at all)
    correct_san: Optional[str]      # the expected move at this position (only meaningful on a miss)
    line_complete: bool
    in_book: bool = False           # informational only: matched some OTHER line in the
                                     # repertoire at this position, even though it wasn't
                                     # accepted — never affects `correct` or the drilled line


class OpeningDrillSession:
    def __init__(self, conn: sqlite3.Connection, repertoire_name: str, my_color: PlayerColor):
        self.conn = conn
        self.repertoire_name = repertoire_name
        self.my_color = my_color
        self.board = chess.Board()
        self.target_path: List[OpeningLine] = []
        self.line_name: Optional[str] = None
        self.path_index = 0

    def start(self, root_id: Optional[str] = None) -> bool:
        """Selects one full line to drill this round — random across the
        repertoire, or within the chosen line when root_id is given (its
        own sub-branches are still picked randomly). Returns False if
        there's nothing to drill."""
        roots = db.root_lines(self.conn, self.repertoire_name, self.my_color.value)
        if root_id is not None:
            roots = [r for r in roots if r.id == root_id]
        if not roots:
            return False

        path = [random.choice(roots)]
        path.extend(self._tree_continuation_from(path[0]))

        self.target_path = path
        self.line_name = path[0].line_name or self.repertoire_name
        self.board = chess.Board()
        self.path_index = 0
        return True

    def _tree_continuation_from(self, node: OpeningLine) -> List[OpeningLine]:
        """Random walk downward from (but not including) node to a leaf,
        following literal database parent-child links — stays within the
        one imported line/tree that `node` belongs to."""
        continuation = []
        current = node
        while True:
            children = db.children_of(self.conn, current.id)
            if not children:
                break
            current = random.choice(children)
            continuation.append(current)
        return continuation

    def _is_book_elsewhere(self, move: chess.Move) -> bool:
        """Informational check: does this move match some OTHER line in
        the repertoire at the current position? Used only for messaging
        (e.g. "that's book theory in a different line") — never changes
        what counts as correct."""
        candidates = db.lines_at_fen(self.conn, self.repertoire_name, self.my_color.value, self.board.fen())
        return any(c.move_uci == move.uci() for c in candidates)

    def is_my_turn(self) -> bool:
        turn_color = PlayerColor.WHITE if self.board.turn == chess.WHITE else PlayerColor.BLACK
        return turn_color == self.my_color

    def is_finished(self) -> bool:
        return self.path_index >= len(self.target_path)

    def play_opponent_move(self) -> Optional[str]:
        """Plays the next (predetermined) opponent move. Returns its SAN,
        or None if the line is already finished."""
        if self.is_finished():
            return None
        node = self.target_path[self.path_index]
        self.board.push(chess.Move.from_uci(node.move_uci))
        self.path_index += 1
        return node.move_san

    def attempt_my_move(self, move: chess.Move) -> DrillResult:
        """Call when you play a move on your turn. Only the exact move on
        the line being drilled counts as correct — a move that happens to
        be valid theory in a DIFFERENT line is still a miss here, since
        you're drilling this specific line, not the repertoire in
        general. On a miss, nothing is pushed and the position is
        unchanged; call this again to retry, or reveal_and_advance()."""
        if self.is_finished():
            return DrillResult(False, None, None, True)

        expected_node = self.target_path[self.path_index]

        if move.uci() == expected_node.move_uci:
            self.board.push(move)
            self.path_index += 1
            return DrillResult(
                correct=True, played_san=expected_node.move_san, correct_san=expected_node.move_san,
                line_complete=self.is_finished(), in_book=True,
            )
        else:
            played_san = self.board.san(move) if move in self.board.legal_moves else None
            return DrillResult(
                correct=False, played_san=played_san, correct_san=expected_node.move_san,
                line_complete=False, in_book=self._is_book_elsewhere(move),
            )

    def reveal_and_advance(self) -> Optional[str]:
        """Plays the correct (targeted) move for you after a miss you
        don't want to retry, and advances. Returns its SAN, or None if
        there's nothing left to reveal (shouldn't normally be called once
        is_finished() is True, but this guards against it regardless)."""
        if self.is_finished():
            return None
        node = self.target_path[self.path_index]
        self.board.push(chess.Move.from_uci(node.move_uci))
        self.path_index += 1
        return node.move_san

    def full_line_san(self) -> List[str]:
        """SAN move list for the whole drilled line, in order — for the
        end-of-line recap."""
        board = chess.Board()
        sans = []
        for node in self.target_path:
            move = chess.Move.from_uci(node.move_uci)
            sans.append(board.san(move))
            board.push(move)
        return sans


def format_move_list(sans: List[str]) -> str:
    """Turns ['d4', 'd5', 'c4', 'e6'] into '1. d4 d5 2. c4 e6'. The first
    move in a target_path is always White's (chess always starts with
    White to move), so numbering starts there regardless of which color
    you're drilling."""
    parts = []
    for i, san in enumerate(sans):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}. {san}")
        else:
            parts.append(san)
    return " ".join(parts)
