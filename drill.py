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

Real-world opponents: with Lichess data (move_share, see explorer.py),
the whole line is picked at once, weighted by how likely real players near
your rating are to follow it — the product of the opponent's move shares
along it (each with a floor, so rare lines still come up). Lines that
differ only in YOUR moves split that weight evenly: every option in your
prep gets drilled. This works the same whether the repertoire is one
branching tree or many separately imported lines. Without data, a line is
picked uniformly as before.

Wrong moves don't auto-correct: attempt_my_move() leaves the board and
position untouched on a miss, so you can just try again, or call
reveal_and_advance() to see the answer.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import chess

import db
from models import OpeningLine, PlayerColor

# a reply played in under this share of games is still drilled this often
MIN_REPLY_WEIGHT = 0.05

# (fen, uci) -> share of games where that move was played there, or None if unknown
ShareLookup = Callable[[str, str], Optional[float]]


@dataclass
class DrillResult:
    correct: bool
    played_san: Optional[str]       # what you played (None if not a legal chess move at all)
    correct_san: Optional[str]      # the expected move at this position (only meaningful on a miss)
    line_complete: bool
    in_book: bool = False           # informational only: matched some OTHER line in the
                                     # repertoire at this position, even though it wasn't
                                     # accepted — never affects `correct` or the drilled line


def _position(fen: str) -> str:
    """FEN without the move counters, so transpositions count as one position."""
    return " ".join(fen.split()[:4])


class OpeningDrillSession:
    def __init__(self, conn: sqlite3.Connection, repertoire_name: str, my_color: PlayerColor,
                 move_share: Optional[ShareLookup] = None):
        self.conn = conn
        self.move_share = move_share
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

        path = self._weighted_line(roots) if self.move_share is not None else None
        if path is None:
            path = [random.choice(roots)]
            path.extend(self._tree_continuation_from(path[0]))

        self.target_path = path
        self.line_name = path[0].line_name or path[0].repertoire_name
        self.board = chess.Board()
        self.path_index = 0
        return True

    def start_line(self, path: List[OpeningLine]) -> bool:
        """Drill exactly this line (e.g. picked in the Tree view)."""
        if not path:
            return False
        self.target_path = list(path)
        self.line_name = path[0].line_name or path[0].repertoire_name
        self.board = chess.Board()
        self.path_index = 0
        return True

    def restart(self) -> None:
        """Same line again from the first move."""
        self.board = chess.Board()
        self.path_index = 0

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

    def _all_lines(self, roots: List[OpeningLine]) -> List[List[OpeningLine]]:
        """Every root-to-leaf path under these roots."""
        lines = []

        def walk(path: List[OpeningLine]) -> None:
            children = db.children_of(self.conn, path[-1].id)
            if not children:
                lines.append(path)
            for child in children:
                walk(path + [child])

        for root in roots:
            walk([root])
        return lines

    def _weighted_line(self, roots: List[OpeningLine]) -> Optional[List[OpeningLine]]:
        """A whole line, picked with the chance a game would follow it: only
        where your lines SPLIT does a choice count. At an opponent's split,
        each reply your lines cover gets its share among those replies (how
        often Lichess players at your level choose it, with a floor so rare
        ones still come up); at your own split, each option in proportion to
        the lines that go that way (so every line of yours gets its turn). A
        position where all your lines go the same way changes nothing — so
        long lines aren't penalised for having more moves.
        None when there's no Lichess data at any of the opponent's splits."""
        lines = self._all_lines(roots)
        offered: Dict[str, set] = {}       # position -> moves your lines play from it
        through: Dict[tuple, int] = {}     # (position, move) -> lines going that way
        for line in lines:
            for node in line:
                if node.fen_before_move:
                    pos = _position(node.fen_before_move)
                    offered.setdefault(pos, set()).add(node.move_uci)
                    through[(pos, node.move_uci)] = through.get((pos, node.move_uci), 0) + 1
        weights, known = [], False
        for line in lines:
            weight = 1.0
            for node in line:
                if not node.fen_before_move:
                    continue
                alts = offered[_position(node.fen_before_move)]
                if len(alts) < 2:
                    continue               # no choice here
                pos = _position(node.fen_before_move)
                if self._is_mine(node.fen_before_move):
                    # your options: in proportion to the lines each one leads to
                    weight *= through[(pos, node.move_uci)] / sum(through[(pos, a)] for a in alts)
                    continue
                shares = {a: self.move_share(node.fen_before_move, a) for a in alts}
                if all(v is None for v in shares.values()):
                    weight /= len(alts)
                    continue
                known = True
                floor = {a: max(v or 0.0, MIN_REPLY_WEIGHT) for a, v in shares.items()}
                weight *= floor[node.move_uci] / sum(floor.values())
            weights.append(weight)
        if not known:
            return None
        return random.choices(lines, weights=weights)[0]

    def _is_mine(self, fen: str) -> bool:
        turn = PlayerColor.WHITE if chess.Board(fen).turn == chess.WHITE else PlayerColor.BLACK
        return turn == self.my_color

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

    def last_move_note(self) -> Optional[str]:
        """The PGN comment (explanation) on the most recently played move
        of the line, if the imported file had one."""
        if self.path_index == 0:
            return None
        return self.target_path[self.path_index - 1].notes

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
