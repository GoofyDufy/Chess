"""Your repertoire as one tree, for the Tree view in Opening Drill — drawn as
a "living tree" (tree_view.py): the starting position is the root, every
point where your lines part is a knot, and each line ends in a leaf.

Lines that share their first moves are merged (separately imported PGN
games included), so 1.d4 Nf6 2.c4 ... is one branch until the lines differ.
A line that stops where others carry on gets its own leaf at that knot.

Pure data + layout, no Tk: build() makes the tree, layout() places it in a
width x height box (growing wider than the box when there are many lines,
for the view to scroll).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import chess

import db
from models import OpeningLine, PlayerColor

LEAF_SPACING = 40        # minimum px between neighbouring leaves
MARGIN_X = 28
MARGIN_RIGHT = 96        # leaf names slant up to the right: room for the last one
TOP = 112                # leaves' row (their names go above, slanted)
BOTTOM_PAD = 34          # the root sits this far above the bottom edge


@dataclass
class Node:
    """One position: reached by `move` (None at the start)."""
    move: Optional[OpeningLine]
    parent: Optional["Node"] = None
    children: List["Node"] = field(default_factory=list)
    line_ends: List[Tuple[str, List[OpeningLine]]] = field(default_factory=list)   # (line name, path)
    # layout
    x: float = 0.0
    y: float = 0.0

    @property
    def path(self) -> List[OpeningLine]:
        out, node = [], self
        while node is not None and node.move is not None:
            out.append(node.move)
            node = node.parent
        return out[::-1]

    @property
    def ply(self) -> int:
        return len(self.path)

    def lines(self) -> List[Tuple[str, List[OpeningLine]]]:
        """Every line ending at or under this position."""
        out = list(self.line_ends)
        for child in self.children:
            out.extend(child.lines())
        return out

    def board(self) -> chess.Board:
        board = chess.Board()
        for node in self.path:
            board.push(chess.Move.from_uci(node.move_uci))
        return board


@dataclass
class Branch:
    """A run of moves with no choice in it: from a knot (or the start) to the
    next knot or a leaf. `nodes` are its positions, the last one is the knot."""
    nodes: List[Node]
    parent: Optional["Branch"] = None
    children: List["Branch"] = field(default_factory=list)
    depth: int = 0
    leaf_index: Optional[int] = None      # set on branches ending in (at least) one leaf
    color_index: int = 0

    @property
    def end(self) -> Node:
        return self.nodes[-1]

    @property
    def lines(self) -> int:
        return len(self.end.lines())

    @property
    def start_xy(self) -> Tuple[float, float]:
        first = self.nodes[0]
        return (first.parent.x, first.parent.y) if first.parent else (first.x, first.y)


@dataclass
class Tree:
    root: Node
    branches: List[Branch]
    leaves: List[Tuple[str, List[OpeningLine], Node]]    # every line: (name, path, end position)
    width: float = 0.0
    height: float = 0.0

    def node_for_path(self, ucis: List[str]) -> Optional[Node]:
        node = self.root
        for uci in ucis:
            node = next((c for c in node.children if c.move.move_uci == uci), None)
            if node is None:
                return None
        return node


def build(conn: sqlite3.Connection, repertoire_name: str, my_color: PlayerColor) -> Tree:
    rows = conn.execute(
        "SELECT * FROM opening_lines WHERE repertoire_name = ? AND my_color = ? AND is_enabled = 1",
        (repertoire_name, my_color.value),
    ).fetchall()
    nodes = [db._row_to_opening_line(r) for r in rows]
    children: Dict[Optional[str], List[OpeningLine]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)

    root = Node(None)
    leaves: List[Tuple[str, List[OpeningLine], Node]] = []

    def walk(line_root: OpeningLine, node: OpeningLine, path: List[OpeningLine]) -> None:
        path = path + [node]
        kids = children.get(node.id, [])
        if not kids:
            pos = root
            for step in path:     # merge with lines sharing the same moves
                nxt = next((c for c in pos.children if c.move.move_uci == step.move_uci), None)
                if nxt is None:
                    nxt = Node(step, parent=pos)
                    pos.children.append(nxt)
                pos = nxt
            name = line_root.line_name or repertoire_name
            pos.line_ends.append((name, path))
            leaves.append((name, path, pos))
        for kid in kids:
            walk(line_root, kid, path)

    for line_root in children.get(None, []):
        walk(line_root, line_root, [])

    branches: List[Branch] = []

    def make_branch(first: Node, parent: Optional[Branch], depth: int) -> Branch:
        run = [first]
        while len(run[-1].children) == 1 and not run[-1].line_ends:
            run.append(run[-1].children[0])
        branch = Branch(run, parent=parent, depth=depth)
        branches.append(branch)
        for child in run[-1].children:
            branch.children.append(make_branch(child, branch, depth + 1))
        return branch

    for child in root.children:
        make_branch(child, None, 1)
    return Tree(root, branches, leaves)


def layout(tree: Tree, width: float, height: float) -> Tree:
    """Leaves spread along the top row (in tree order), each knot centred over
    the leaves it leads to, knots stepping up from the root by depth."""
    slots: List[Branch] = []          # one slot per leaf, left to right

    def order(branch: Branch) -> None:
        if branch.end.line_ends:      # a line ends here (maybe others carry on)
            branch.leaf_index = len(slots)
            slots.append(branch)
        for child in branch.children:
            order(child)

    top_branches = [b for b in tree.branches if b.parent is None]
    for b in top_branches:
        order(b)
    n = max(1, len(slots))
    tree.width = max(width, MARGIN_X + MARGIN_RIGHT + (n - 1) * LEAF_SPACING)
    tree.height = height
    span = tree.width - MARGIN_X - MARGIN_RIGHT
    xs = {id(b): MARGIN_X + (span * i / (n - 1) if n > 1 else span / 2) for i, b in enumerate(slots)}

    max_depth = max((b.depth for b in tree.branches), default=1)
    root_y = height - BOTTOM_PAD
    knot_band = (root_y - TOP) * 0.78          # knots live in the lower part; leaves on top
    step = knot_band / max(1, max_depth)

    def place(branch: Branch) -> float:
        child_x = [place(c) for c in branch.children]
        own = [xs[id(branch)]] if id(branch) in xs else []
        x = sum(child_x + own) / len(child_x + own)
        branch.end.x = x
        branch.end.y = TOP if not branch.children else root_y - branch.depth * step
        return x

    for b in top_branches:
        place(b)
    tree.root.x = sum(b.end.x for b in top_branches) / max(1, len(top_branches)) if top_branches else tree.width / 2
    tree.root.y = root_y

    # positions along each branch, on its curve (the knot is the last one)
    for b in tree.branches:
        (x0, y0), (x1, y1) = b.start_xy, (b.end.x, b.end.y)
        for i, node in enumerate(b.nodes[:-1], start=1):
            node.x, node.y = bezier(x0, y0, x1, y1, i / len(b.nodes))
    # colours: one per leaf, families kept apart by the top-level branch
    for i, b in enumerate(slots):
        b.color_index = i
    return tree


def curve(x0: float, y0: float, x1: float, y1: float) -> List[float]:
    """Control points of a branch: leaves the knot straight up, arrives straight up."""
    rise = y0 - y1
    return [x0, y0, x0, y0 - rise * 0.5, x1, y1 + rise * 0.5, x1, y1]


def bezier(x0: float, y0: float, x1: float, y1: float, t: float) -> Tuple[float, float]:
    p = curve(x0, y0, x1, y1)
    u = 1 - t
    x = u ** 3 * p[0] + 3 * u * u * t * p[2] + 3 * u * t * t * p[4] + t ** 3 * p[6]
    y = u ** 3 * p[1] + 3 * u * u * t * p[3] + 3 * u * t * t * p[5] + t ** 3 * p[7]
    return x, y


def thickness(lines: int) -> float:
    """Branch width grows with the lines running through it."""
    return 3 + 2.6 * math.sqrt(max(1, lines))


# ---------- where Lichess players leave your lines ----------

MIN_DEVIATION_SHARE = 0.08     # a reply played in at least 8% of games there...
MIN_DEVIATION_GAMES = 30       # ...with enough games to mean something
DEVIATIONS_PER_POSITION = 3


@dataclass
class Deviation:
    node: Node                 # the position (opponent to move, your prep continues)
    uci: str
    san: str                   # e.g. "7...c5"
    share: float               # of games at that position
    games: int
    score: float               # how the players choosing it score


def deviations(tree: Tree, stats_for, my_color: PlayerColor) -> Dict[int, List[Deviation]]:
    """id(node) -> popular opponent replies your lines don't cover, most
    played first. stats_for(fen) -> explorer.PositionStats or None (cache
    only). Only where your prep goes on with an opponent move — a line that
    stops is a deliberate stopping point."""
    mine = chess.WHITE if my_color == PlayerColor.WHITE else chess.BLACK
    out: Dict[int, List[Deviation]] = {}
    stack = [tree.root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if not node.children:
            continue
        board = node.board()
        if board.turn == mine:
            continue
        stats = stats_for(board.fen())
        if stats is None or not stats.games:
            continue
        covered = {c.move.move_uci for c in node.children}
        found = []
        for m in stats.moves:
            share = m.games / stats.games
            if m.uci in covered or share < MIN_DEVIATION_SHARE or m.games < MIN_DEVIATION_GAMES:
                continue
            try:
                move = chess.Move.from_uci(m.uci)
                san = board.san(move)
            except (ValueError, AssertionError):
                continue
            number = board.fullmove_number
            prefix = f"{number}." if board.turn == chess.WHITE else f"{number}..."
            found.append(Deviation(node, m.uci, prefix + san, share, m.games, m.score_for(board.turn)))
        if found:
            found.sort(key=lambda d: -d.share)
            out[id(node)] = found[:DEVIATIONS_PER_POSITION]
    return out


# ---------- where an opening starts ----------
# Before that, the opponent's moves are OTHER openings (1...d5 against your
# Catalan isn't a Catalan deviation); from there on, they're deviations from
# your lines. Default: where all the repertoire's lines still agree (its trunk);
# a player can move it (saved per repertoire in settings).

def _start_key(repertoire_name: str, my_color: PlayerColor) -> str:
    return f"opening_start|{repertoire_name}|{my_color.value}"


def default_start(tree: Tree) -> Node:
    tops = [b for b in tree.branches if b.parent is None]
    return tops[0].end if len(tops) == 1 else tree.root


def start_node(conn: sqlite3.Connection, tree: Tree, repertoire_name: str, my_color: PlayerColor) -> Node:
    saved = db.get_setting(conn, _start_key(repertoire_name, my_color))
    if saved is not None:
        node = tree.node_for_path(saved.split()) if saved else tree.root
        if node is not None:
            return node
    return default_start(tree)


def set_start(conn: sqlite3.Connection, repertoire_name: str, my_color: PlayerColor,
              node: Optional[Node]) -> None:
    """node None: back to the default."""
    key = _start_key(repertoire_name, my_color)
    if node is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()
    else:
        db.set_setting(conn, key, " ".join(m.move_uci for m in node.path))


def start_path(conn: sqlite3.Connection, repertoire_name: str, my_color: PlayerColor) -> List[str]:
    """The start position as moves (for code that has no tree at hand)."""
    tree = build(conn, repertoire_name, my_color)
    return [m.move_uci for m in start_node(conn, tree, repertoire_name, my_color).path]


def is_inside(node: Node, start: Node) -> bool:
    """At or after the opening's start."""
    while node is not None:
        if node is start:
            return True
        node = node.parent
    return False
