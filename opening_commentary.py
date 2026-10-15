"""Why your deviation from your prep is (or isn't) worse — Opening Drill →
My games. Every sentence comes from a computed fact, like explain.py:

  - Stockfish: both moves searched side by side (root_moves) — the
    evaluation of each, and its main line after your move;
  - opening principles, from the board: development, moving a piece twice,
    an early queen, giving up castling, blocking your own c/d/e/f pawn,
    the centre (d4/e4/d5/e5), a bishop's scope, a loose piece, pawn cover
    in front of the king, giving up the bishop pair;
  - what the opponent's best reply does to you (attacks, check, tempo);
  - your own results with each move there, and how Lichess players at your
    level play and score them (from the explorer cache).

The engine part runs on a background thread (callers do that); results are
saved in deviation_notes, so a deviation is only worked out once.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import chess
import chess.engine

import explain
from analyzer import MATE_CP, _score_to_cp

DEPTH = 18
LINE_PLIES = 8
CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
OPENING_PLY = 24
MATERIAL_COST_CP = 60      # "wins material by force" only when your move also costs this much
VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS deviation_notes (
    key TEXT PRIMARY KEY,          -- position | your move | prep moves
    version INTEGER,
    data TEXT,                     -- Notes as JSON
    created_at TEXT
);
"""

_MINOR_HOMES = {chess.WHITE: {chess.B1: chess.KNIGHT, chess.G1: chess.KNIGHT, chess.C1: chess.BISHOP,
                              chess.F1: chess.BISHOP},
                chess.BLACK: {chess.B8: chess.KNIGHT, chess.G8: chess.KNIGHT, chess.C8: chess.BISHOP,
                              chess.F8: chess.BISHOP}}


_KNIGHT_SQUARES = {chess.WHITE: {chess.G1: (chess.F3,), chess.B1: (chess.C3,)},
                   chess.BLACK: {chess.G8: (chess.F6,), chess.B8: (chess.C6,)}}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass
class Fact:
    good: bool
    text: str


@dataclass
class Notes:
    mine_san: str
    prep_san: str
    mine_cp: int
    prep_cp: int
    verdict: str
    prep_does: List[str] = field(default_factory=list)       # sentences
    mine_does: List[str] = field(default_factory=list)
    punishment: str = ""                                      # what the opponent's reply does
    mine_line: str = ""
    prep_line: str = ""

    @property
    def cost(self) -> int:
        return self.prep_cp - self.mine_cp


# ---------- board facts about one opening move ----------

def _undeveloped(board: chess.Board, color: chess.Color) -> int:
    return sum(1 for sq, kind in _MINOR_HOMES[color].items()
               if (p := board.piece_at(sq)) is not None and p.color == color and p.piece_type == kind)


def _centre_hits(board: chess.Board, color: chess.Color) -> set:
    return {sq for sq in CENTRE if board.attackers(color, sq) or
            ((p := board.piece_at(sq)) is not None and p.color == color and p.piece_type == chess.PAWN)}


def _moved_before(board: chess.Board, square: int) -> bool:
    """Did a piece arrive on this square earlier in the game (so moving it is a second move)?"""
    return any(m.to_square == square for m in board.move_stack)


def principles(board: chess.Board, move: chess.Move) -> List[Fact]:
    """What this move does in opening terms (board = position before it, with its history)."""
    me = board.turn
    piece = board.piece_at(move.from_square)
    if piece is None:
        return []
    name = chess.piece_name(piece.piece_type)
    after = board.copy()
    after.push(move)
    facts: List[Fact] = []
    early = board.ply() < OPENING_PLY
    capture = board.is_capture(move)

    if board.is_castling(move):
        facts.append(Fact(True, "castles — the king is safe and a rook joins the game"))
    elif piece.piece_type in (chess.KNIGHT, chess.BISHOP) and _MINOR_HOMES[me].get(move.from_square) == piece.piece_type:
        facts.append(Fact(True, f"develops the {name}"))
    elif early and piece.piece_type in (chess.KNIGHT, chess.BISHOP, chess.QUEEN) and not capture \
            and not after.is_check() and _moved_before(board, move.from_square):
        facts.append(Fact(False, f"moves the {name} a second time — a lost tempo while "
                                 f"{_undeveloped(board, me)} minor piece(s) still wait at home"
                          if _undeveloped(board, me) else f"moves the {name} a second time"))
    if piece.piece_type == chess.QUEEN and early and _undeveloped(board, me) >= 2 and not capture:
        facts.append(Fact(False, "brings the queen out before the minor pieces — it can be chased with tempo"))
    if piece.piece_type == chess.KING and not board.is_castling(move) and board.has_castling_rights(me):
        facts.append(Fact(False, "moves the king and gives up castling"))
    if piece.piece_type == chess.ROOK and board.has_castling_rights(me) and \
            board.castling_rights != after.castling_rights:
        facts.append(Fact(False, "gives up castling on that side"))

    # standing in front of your own c/d/e pawn that hasn't moved (in front of the
    # f-pawn, like Nf3, is normal)
    home_rank, step = (1, 8) if me == chess.WHITE else (6, -8)
    behind = move.to_square - step
    if chess.square_file(move.to_square) in (2, 3, 4) and piece.piece_type != chess.PAWN and \
            chess.square_rank(behind) == home_rank and (p := board.piece_at(behind)) is not None and \
            p.color == me and p.piece_type == chess.PAWN:
        file = "cde"[chess.square_file(move.to_square) - 2]
        facts.append(Fact(False, f"stands in front of your {file}-pawn, so it can't advance "
                                 f"({file}{4 if me == chess.WHITE else 5} is off for now)"))

    # the centre
    before_c, after_c = _centre_hits(board, me), _centre_hits(after, me)
    gained = sorted(chess.square_name(s) for s in after_c - before_c)
    if gained:
        facts.append(Fact(True, f"adds control of {' and '.join(gained)} in the centre"))

    # a pawn on the square your undeveloped knight wants (f3 for the g1 knight, ...)
    if piece.piece_type == chess.PAWN and not capture:
        for home, kind in _MINOR_HOMES[me].items():
            if kind == chess.KNIGHT and (p := board.piece_at(home)) is not None and p.piece_type == chess.KNIGHT \
                    and p.color == me and move.to_square in _KNIGHT_SQUARES[me].get(home, ()):
                facts.append(Fact(False, f"takes the {chess.square_name(move.to_square)} square from your knight "
                                         f"on {chess.square_name(home)} — its most natural square"))
        # an f-pawn push with the king still in the middle: the e1-h4 / e8-h5 diagonal opens
        king = board.king(me)
        if chess.square_file(move.from_square) == 5 and king in (chess.E1, chess.E8) and early:
            facts.append(Fact(False, "weakens the diagonal to your king, which is still in the middle"))

    # flank pawn before development
    if piece.piece_type == chess.PAWN and chess.square_file(move.from_square) in (0, 1, 6, 7) and early \
            and _undeveloped(board, me) >= 3 and not capture:
        facts.append(Fact(False, "is a flank pawn move while your pieces are still at home"))

    # bishop pair
    if capture and piece.piece_type == chess.BISHOP and (v := board.piece_at(move.to_square)) is not None \
            and v.piece_type == chess.KNIGHT and len(board.pieces(chess.BISHOP, me)) == 2 \
            and len(board.pieces(chess.BISHOP, not me)) == 2:
        facts.append(Fact(False, "gives up the bishop pair"))

    for note in explain._scope_notes(board, after, me, move.to_square):
        facts.append(Fact(note.startswith("it opens"), note.replace("it ", "", 1)))
    hanging = explain._newly_hanging(board, after, me)
    if hanging:
        facts.append(Fact(False, f"leaves your {hanging[0]} loose"))
    weak = explain._king_weakening(board, move, me)
    if weak:
        facts.append(Fact(False, weak.replace("it ", "", 1)))
    return facts


# ---------- the comparison (engine: background thread only) ----------

def _cp(score: chess.engine.PovScore, color: chess.Color) -> int:
    return _score_to_cp(score, color)


def _fmt(cp: int) -> str:
    if abs(cp) >= MATE_CP // 2:
        return "a forced mate" if cp > 0 else "getting mated"
    return f"{cp / 100:+.2f}".replace("-", "−")


def compare(engine: chess.engine.SimpleEngine, board: chess.Board, mine: chess.Move,
            prep: List[chess.Move]) -> Notes:
    """board: the position with its game history; mine: what you played; prep: your repertoire's move(s)."""
    me = board.turn
    roots = list(dict.fromkeys(prep + [mine]))
    lines = engine.analyse(board, chess.engine.Limit(depth=DEPTH), multipv=len(roots), root_moves=roots)
    by_move = {info["pv"][0]: info for info in lines if info.get("pv")}
    best_prep = max((m for m in prep if m in by_move), key=lambda m: _cp(by_move[m]["score"], me), default=prep[0])
    mine_info, prep_info = by_move.get(mine), by_move.get(best_prep)
    mine_cp = _cp(mine_info["score"], me) if mine_info else 0
    prep_cp = _cp(prep_info["score"], me) if prep_info else 0
    mine_san, prep_san = board.san(mine), board.san(best_prep)
    notes = Notes(mine_san, prep_san, mine_cp, prep_cp, "")

    cost = prep_cp - mine_cp
    numbers = f"{prep_san} {_fmt(prep_cp)}, {mine_san} {_fmt(mine_cp)}"
    if cost >= 100:
        notes.verdict = f"{mine_san} is a real mistake here — Stockfish: {numbers}."
    elif cost >= 40:
        notes.verdict = f"{mine_san} is clearly weaker than your prep move {prep_san} — Stockfish: {numbers}."
    elif cost >= 15:
        notes.verdict = (f"{mine_san} is slightly weaker than {prep_san} — not a mistake, but it gives away some "
                         f"of your edge (Stockfish: {numbers}).")
    elif cost > -15:
        notes.verdict = (f"Stockfish rates {mine_san} about the same as {prep_san} ({numbers}) — a matter of "
                         "taste. If you like it, add it to your repertoire instead.")
    else:
        notes.verdict = (f"Stockfish actually prefers your {mine_san} to your prep move {prep_san} ({numbers}) — "
                         "consider changing your repertoire.")

    prep_facts, mine_facts = principles(board, best_prep), principles(board, mine)
    prep_texts = {f.text for f in prep_facts}
    # what your prep move achieves that yours doesn't, and yours' own problems
    notes.prep_does = [f.text for f in prep_facts if f.good and f.text not in {m.text for m in mine_facts}][:3]
    notes.mine_does = [f.text for f in mine_facts if not f.good and f.text not in prep_texts][:3]
    if not notes.mine_does:
        notes.mine_does = [f.text for f in mine_facts if f.good][:2]

    if mine_info:
        after = board.copy()
        after.push(mine)
        pv = list(mine_info["pv"][1:LINE_PLIES + 1])
        notes.mine_line = explain._line(after, pv, LINE_PLIES) if pv else ""
        if pv and pv[0] in after.legal_moves:
            reply = pv[0]
            feats = explain._move_features(after, reply)
            gets = explain._line_outcome(after, pv, me)
            opp = "White" if after.turn == chess.WHITE else "Black"
            reply_san = after.san(reply)
            # only what the evaluation backs up: material "won" in a line that
            # Stockfish still calls equal is only taken for a while
            if gets <= -1 and cost >= MATERIAL_COST_CP:
                notes.punishment = (f"{opp}'s best answer {reply_san} wins {explain._material_words(-gets)} "
                                    f"by force: {notes.mine_line}.")
            elif feats and cost >= 15:
                notes.punishment = f"{opp}'s best answer {reply_san} {', '.join(feats)}."
            else:
                rf = principles(after, reply)
                good = [f.text.replace("your ", "their ") for f in rf if f.good]   # their move
                if good and cost >= 15:
                    notes.punishment = f"{opp} answers {reply_san}, which {good[0]}."
    if prep_info:
        after = board.copy()
        after.push(best_prep)
        pv = list(prep_info["pv"][1:LINE_PLIES + 1])
        notes.prep_line = explain._line(after, pv, LINE_PLIES) if pv else ""
    return notes


def text(n: Notes) -> str:
    """The commentary as a few short paragraphs."""
    parts = [n.verdict]
    if n.prep_does:
        parts.append(f"Your prep move {n.prep_san} " + "; ".join(n.prep_does) + ".")
    if n.mine_does:
        parts.append(f"{n.mine_san} " + "; ".join(n.mine_does) + ".")
    if n.punishment:
        parts.append(n.punishment)
    if n.mine_line and n.cost >= 15 and n.mine_line not in n.punishment:
        parts.append(f"Stockfish's line after {n.mine_san}: {n.mine_line}.")
    return "\n\n".join(parts)


# ---------- cache ----------

def _key(fen: str, mine: str, prep: List[str]) -> str:
    return f"{' '.join(fen.split()[:4])}|{mine}|{','.join(sorted(prep))}"


def cached(conn: sqlite3.Connection, fen: str, mine: str, prep: List[str]) -> Optional[Notes]:
    row = conn.execute("SELECT data FROM deviation_notes WHERE key = ? AND version = ?",
                       (_key(fen, mine, prep), VERSION)).fetchone()
    return Notes(**json.loads(row[0])) if row else None


def save(conn: sqlite3.Connection, fen: str, mine: str, prep: List[str], notes: Notes) -> None:
    conn.execute("INSERT OR REPLACE INTO deviation_notes (key, version, data, created_at) VALUES (?, ?, ?, ?)",
                 (_key(fen, mine, prep), VERSION, json.dumps(asdict(notes)), dt.datetime.now().isoformat()))
    conn.commit()
