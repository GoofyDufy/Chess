"""Is an opening line sharp or quiet? A label and a short description for
each line of the opening library, from computed facts at the line's end
position (no guessing, like explain.py):

  - the board: a gambit (one side a pawn or more down), pawns in contact in
    the centre (tension) or locked face to face (closed), open files,
    queens traded, a symmetrical pawn structure;
  - the name: "Gambit", "Countergambit", "Attack", "Sacrifice";
  - Stockfish (MultiPV 3): only one good move (the best beats the 2nd by a
    lot — mistakes are punished) or several about equal (forgiving), and
    whether one side is already clearly better.

Each fact scores toward sharp (+) or quiet (−); the total gives Sharp /
Balanced / Quiet. The engine part runs on a background thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import chess
import chess.engine

from analyzer import MATE_CP, _score_to_cp, win_chances
from explain import VALUES
from opening_library import Opening

DEPTH = 12
SHARP_AT = 3               # score >= this: Sharp
QUIET_AT = -1              # score <= this: Quiet
SETTLE_PLIES = 4           # recaptures along Stockfish's line played out before counting material
LOOK_AHEAD = 12            # plies of Stockfish's main line checked for opposite-side castling
MIN_PV_FOR_MATERIAL = 8    # material down counts only if still down after this long a line (or a named gambit)
ONLY_MOVE_GAP = 0.15       # winning chances (-1..1) between the best and the 2nd: "only one good move"
SEVERAL_CP = 25            # the best three within this: "several good moves"
CLEAR_EDGE_CP = 90
VERSION = 1
CENTRE_FILES = (2, 3, 4, 5)        # c-f


@dataclass
class Character:
    label: str                 # "Sharp" / "Balanced" / "Quiet"
    score: int
    text: str                  # one or two sentences, from the facts
    eval_cp: Optional[int]     # White's point of view


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUES[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())


def settled(board: chess.Board, pv: List[chess.Move]) -> chess.Board:
    """The position once pending captures are played out along Stockfish's line
    (a line ending on 3.exd5 isn't a pawn up: Black takes back)."""
    b = board.copy()
    for move in pv[:SETTLE_PLIES]:
        if move not in b.legal_moves or not b.is_capture(move):
            break
        b.push(move)
    return b


def board_facts(board: chess.Board, name: str, eval_cp: Optional[int] = None,
                later_balance: Optional[int] = None) -> List[Tuple[int, str]]:
    """(score, phrase) facts from the (settled) position and the name."""
    facts: List[Tuple[int, str]] = []
    lower = name.lower()
    if "countergambit" in lower:
        facts.append((2, "a countergambit"))
    elif "gambit" in lower and "declined" not in lower:
        facts.append((2, "a gambit"))
    if "attack" in lower or "sacrifice" in lower:
        facts.append((1, "an attacking line"))

    balance = _material(board, chess.WHITE)
    # only material still missing at the end of Stockfish's line counts (a pawn
    # taken now and won back three moves later is no gambit)
    named_gambit = "gambit" in lower and "declined" not in lower
    lasting = named_gambit or (later_balance is not None and later_balance * balance > 0 and abs(later_balance) >= 1)
    if abs(balance) >= 1 and lasting:
        side = "White" if balance < 0 else "Black"
        worth = f"{abs(balance)} pawn{'s' if abs(balance) > 1 else ''}"
        what = f"{side} is a pawn down" if abs(balance) == 1 else f"{side} has given up material (worth {worth})"
        # down but not worse (by Stockfish): material given for activity
        fair = eval_cp is None or abs(eval_cp) < 120
        facts.append((2, f"{what} for activity" if fair else what))

    if not board.pieces(chess.QUEEN, chess.WHITE) and not board.pieces(chess.QUEEN, chess.BLACK):
        facts.append((-2, "the queens are off"))

    wp, bp = board.pieces(chess.PAWN, chess.WHITE), board.pieces(chess.PAWN, chess.BLACK)
    # pawns attacking each other in the centre (c-f files): tension
    tension = any(chess.square_file(sq) in CENTRE_FILES and board.attacks(sq) & bp for sq in wp)
    # pawns blocked face to face on the d/e files: a closed centre
    locked = sum(1 for sq in wp if chess.square_file(sq) in (3, 4) and sq + 8 < 64 and (sq + 8) in bp)
    open_files = [f for f in range(8) if not any(chess.square_file(s) == f for s in wp | bp)]
    if tension:
        facts.append((1, "pawns are in contact in the centre"))
    if locked >= 2 and not tension:                 # one blocked pair (e4/e5) is just an open game
        facts.append((-2, "the centre is locked"))
    if len(open_files) >= 2:
        facts.append((1, f"{len(open_files)} files are open"))
    mirror = {chess.square_mirror(sq) for sq in bp}
    if wp == mirror and len(wp) >= 6:
        facts.append((-1, "the pawn structure is symmetrical"))
    return facts


def engine_facts(engine: chess.engine.SimpleEngine, board: chess.Board):
    """Facts from Stockfish at the end position, its evaluation (White's view) and main line."""
    if board.is_game_over():
        return [], None, []
    infos = engine.analyse(board, chess.engine.Limit(depth=DEPTH), multipv=3)
    me = board.turn
    cps = [_score_to_cp(i["score"], me) for i in infos if i.get("pv")]
    if not cps:
        return [], None, []
    side = "White" if me == chess.WHITE else "Black"
    facts: List[Tuple[int, str]] = []
    if len(cps) >= 2 and win_chances(cps[0]) - win_chances(cps[1]) >= ONLY_MOVE_GAP:
        facts.append((2, f"{side} has only one good move here — mistakes are punished"))
    elif len(cps) >= 3 and cps[0] - cps[2] <= SEVERAL_CP:
        facts.append((-1, f"{side} has several good moves — it's forgiving"))
    white_cp = cps[0] if me == chess.WHITE else -cps[0]
    if abs(white_cp) >= CLEAR_EDGE_CP and abs(white_cp) < MATE_CP // 2:
        facts.append((1, f"{'White' if white_cp > 0 else 'Black'} is already clearly better ({white_cp / 100:+.1f})"))
    pv = list(infos[0].get("pv", []))
    # along the main line: do the kings end up on opposite wings? (pawn storms, a race)
    b = board.copy()
    for move in pv[:LOOK_AHEAD]:
        if move not in b.legal_moves:
            break
        b.push(move)
    wk, bk = b.king(chess.WHITE), b.king(chess.BLACK)
    if wk is not None and bk is not None:
        wf, bf = chess.square_file(wk), chess.square_file(bk)
        if (wf <= 2 and bf >= 5) or (wf >= 5 and bf <= 2):
            facts.append((2, "in Stockfish's main line the kings castle on opposite sides — a race"))
    return facts, white_cp, pv


def describe(engine: Optional[chess.engine.SimpleEngine], opening: Opening) -> Character:
    board = chess.Board()
    for uci in opening.ucis:
        board.push_uci(uci)
    eval_cp, pv, more = None, [], []
    if engine is not None:
        more, eval_cp, pv = engine_facts(engine, board)
    later = None                                     # a short line can't tell lasting from temporary
    if len(pv) >= MIN_PV_FOR_MATERIAL:
        end = board.copy()
        for move in pv:
            if move not in end.legal_moves:
                break
            end.push(move)
        later = _material(end, chess.WHITE)
    facts = board_facts(settled(board, pv), opening.name, eval_cp, later) + more
    score = sum(s for s, _ in facts)
    label = "Sharp" if score >= SHARP_AT else "Quiet" if score <= QUIET_AT else "Balanced"
    why = [t for s, t in sorted(facts, key=lambda f: -abs(f[0])) if (s > 0) == (label == "Sharp") or label == "Balanced"]
    text = (why[0][0].upper() + "; ".join(why[:3])[1:] + "." if why else
            "Nothing forcing at the end of this line — a normal developing position.")
    return Character(label, score, text, eval_cp)
