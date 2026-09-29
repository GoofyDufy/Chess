"""
Plain-English explanations for puzzle moves, grounded in real analysis:
Stockfish's actual follow-up lines (short fresh searches) plus concrete
board facts — material won/lost along the line, forks, checks, pieces left
hanging, king-side pawn weakening, and how much scope a bishop/rook/queen
gains or loses ("blocks your bishop on c1: 6 squares -> 1").

Nothing here is guessed: every sentence comes from a computed fact. Without
Stockfish the explanations fall back to the board facts only.

Stockfish calls are slow-ish (~0.3 s each), so callers run these on a
background thread (puzzle_tab does, via its UI queue).
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

import chess
import chess.engine

from analyzer import MATE_CP

THINK_SECONDS = 0.3
LINE_PLIES = 6            # how far to follow an engine line when counting material
SCOPE_DIFF = 3            # squares of mobility difference worth mentioning
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
_MATERIAL_WORDS = {1: "a pawn", 2: "two pawns", 3: "a piece", 4: "a piece and a pawn", 5: "a rook",
                   6: "a rook and a pawn", 8: "a rook and a piece", 9: "the queen"}


# ---------- board facts ----------

def _balance(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.pieces(pt, color)) * v - len(board.pieces(pt, not color)) * v
               for pt, v in VALUES.items())


def _material_words(n: int) -> str:
    return _MATERIAL_WORDS.get(n, f"{n} points of material")


def _side(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def _piece_on(board: chess.Board, square: int) -> str:
    piece = board.piece_at(square)
    return f"{chess.piece_name(piece.piece_type)} on {chess.square_name(square)}" if piece else "?"


def _line(board: chess.Board, moves: List[chess.Move], plies: int = 4) -> str:
    """Numbered SAN from this position, e.g. '14...Nxe4 15.Nxe4 Qxb2'."""
    b, parts = board.copy(stack=False), []
    for i, move in enumerate(moves[:plies]):
        if move not in b.legal_moves:
            break
        if b.turn == chess.WHITE:
            parts.append(f"{b.fullmove_number}.{b.san(move)}")
        else:
            parts.append(f"{b.fullmove_number}...{b.san(move)}" if i == 0 else b.san(move))
        b.push(move)
    return " ".join(parts)


def _line_noted(board: chess.Board, moves: List[chess.Move], plies: int = 4, losing: bool = False) -> str:
    """Like _line, but with what the FIRST move does in brackets right after
    it: '21...Bxc3+ (takes the knight, gives check) 22.Qxc3 Rh1+'.
    losing=True: the line wins material for the mover, so a capture is never
    called a 'trade'."""
    tokens = _line(board, moves, plies).split(" ")
    if not moves or not tokens[0]:
        return ""
    notes = _move_features(board, moves[0])
    if losing:
        notes = [re.sub(r"^trades (\w+)s$", r"takes the \1", n) for n in notes]
    if notes:
        tokens[0] += f" ({', '.join(notes)})"
    return " ".join(tokens)


def _mobility(board: chess.Board, square: int) -> int:
    piece = board.piece_at(square)
    if piece is None:
        return 0
    b = board.copy(stack=False)
    b.turn = piece.color
    return sum(1 for m in b.pseudo_legal_moves if m.from_square == square)


def _scope_notes(before: chess.Board, after: chess.Board, color: chess.Color, moved_to: int) -> List[str]:
    """Long-range pieces (other than the one that moved) whose scope changed a lot."""
    notes = []
    for square, piece in before.piece_map().items():
        if piece.color != color or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        if square == moved_to or after.piece_at(square) != piece:
            continue
        a, b = _mobility(before, square), _mobility(after, square)
        if b <= a - SCOPE_DIFF:
            notes.append(f"it blocks your {_piece_on(before, square)} ({a} squares → {b})")
        elif b >= a + SCOPE_DIFF:
            notes.append(f"it opens up your {_piece_on(before, square)} ({a} squares → {b})")
    return notes


def _newly_hanging(before: chess.Board, after: chess.Board, color: chess.Color) -> List[str]:
    """Your pieces (knight or better, or any pawn) that were safe before the
    move and are attacked-but-undefended after it."""
    out = []
    for square, piece in after.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        hanging_now = after.attackers(not color, square) and not after.attackers(color, square)
        was_hanging = (before.piece_at(square) == piece and before.attackers(not color, square)
                       and not before.attackers(color, square))
        if hanging_now and not was_hanging:
            out.append(_piece_on(after, square))
    return out


def _king_weakening(before: chess.Board, move: chess.Move, color: chess.Color) -> Optional[str]:
    """Pushing a pawn in front of your castled king."""
    piece = before.piece_at(move.from_square)
    king = before.king(color)
    if piece is None or piece.piece_type != chess.PAWN or king is None:
        return None
    if abs(chess.square_file(move.from_square) - chess.square_file(king)) <= 1 and \
            chess.square_distance(move.from_square, king) <= 2 and chess.square_file(king) in (0, 1, 2, 5, 6, 7):
        return "it loosens the pawn cover in front of your king"
    return None


def _move_features(board: chess.Board, move: chess.Move) -> List[str]:
    """What a single move does immediately: capture, check, fork, threat."""
    mover = board.turn
    notes = []
    captured = board.piece_at(move.to_square)
    if board.is_en_passant(move):
        notes.append("captures a pawn en passant")
    elif captured:
        mover_piece = board.piece_at(move.from_square)
        recapturable = board.copy(stack=False)
        recapturable.push(move)
        if captured.piece_type == mover_piece.piece_type and recapturable.attackers(not mover, move.to_square):
            notes.append(f"trades {chess.piece_name(captured.piece_type)}s")
        else:
            notes.append(f"takes the {chess.piece_name(captured.piece_type)}")
    if move.promotion:
        notes.append(f"promotes to a {chess.piece_name(move.promotion)}")
    if board.is_castling(move):
        notes.append("castles the king to safety")
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return ["checkmate"]
    if after.is_check():
        notes.append("gives check")
    # fork: the moved piece hits two valuable enemy pieces (king counts)
    mover_value = VALUES[after.piece_at(move.to_square).piece_type]
    valuable, threats = [], []
    for sq in after.attacks(move.to_square):
        p = after.piece_at(sq)
        if p is None or p.color == mover:
            continue
        if p.piece_type == chess.KING or VALUES[p.piece_type] >= 3:
            valuable.append(chess.piece_name(p.piece_type))
        # a real threat: the target is undefended or worth more than the attacker
        if p.piece_type != chess.KING and VALUES[p.piece_type] >= 3 and (
                not after.attackers(not mover, sq) or VALUES[p.piece_type] > mover_value):
            threats.append(f"{chess.piece_name(p.piece_type)} on {chess.square_name(sq)}")
    if len(valuable) >= 2:
        a, b = valuable[:2]
        notes.append(f"forks both {a}s" if a == b else f"forks the {a} and {b}")
    elif threats and not captured:
        notes.append(f"attacks the {threats[0]}")
    return notes


# ---------- engine lines ----------

def _engine_line(engine, board: chess.Board) -> Tuple[List[chess.Move], Optional[chess.engine.PovScore]]:
    if engine is None or board.is_game_over():
        return [], None
    info = engine.analyse(board, chess.engine.Limit(time=THINK_SECONDS))
    return list(info.get("pv", []))[:LINE_PLIES], info.get("score")


def _line_outcome(board: chess.Board, pv: List[chess.Move], me: chess.Color) -> int:
    """Material change for `me` after playing the line."""
    b, start = board.copy(stack=False), _balance(board, me)
    for move in pv:
        if move not in b.legal_moves:
            break
        b.push(move)
    return _balance(b, me) - start


def _mate_for(score: Optional[chess.engine.PovScore], color: chess.Color) -> Optional[int]:
    if score is None:
        return None
    pov = score.pov(color)
    return pov.mate() if pov.is_mate() and pov.mate() > 0 else None


# ---------- public ----------

def explain_attempt(fen: str, attempt: chess.Move, engine=None, with_line: bool = False):
    """Why a wrong try fails — WITHOUT naming the best move, so the puzzle
    can still be retried."""
    before = chess.Board(fen)
    me = before.turn
    after = before.copy(stack=False)
    after.push(attempt)
    san = before.san(attempt)
    sentences = []

    pv, score = _engine_line(engine, after)
    opp = _side(not me)
    if pv:
        reply = _line_noted(after, pv, 4, losing=_line_outcome(after, pv, me) <= -1)
        mate = _mate_for(score, not me)
        change = _line_outcome(after, pv, me)
        if mate:
            sentences.append(f"After {san}, {opp} has a forced mate in {mate}: {reply}.")
        elif change <= -1:
            sentences.append(f"After {san}, {opp} answers {reply} and you lose {_material_words(-change)}.")
        else:
            cp = score.pov(me).score(mate_score=MATE_CP) if score else None
            evaluation = f" (evaluation {cp / 100:+.1f})" if cp is not None else ""
            sentences.append(f"After {san}, {opp}'s best answer is {reply}{evaluation} — "
                             "you miss something stronger here.")

    facts = _newly_hanging(before, after, me)
    if facts:
        sentences.append(f"{san} leaves your {facts[0]} undefended.")
    extra = [n for n in [_king_weakening(before, attempt, me)] + _scope_notes(before, after, me, attempt.to_square) if n]
    if extra:
        sentences.append(f"Also, {extra[0]}.")
    if not sentences:
        sentences.append(f"{san} lets {opp} off the hook — look for a more forcing move.")
    text = " ".join(sentences[:3])
    # with_line: also return the engine's refutation (list of moves from the
    # position after the attempt), so the board can draw the reply
    return (text, pv) if with_line else text


def explain_solution(fen: str, best: chess.Move, game_move: Optional[chess.Move], engine=None) -> str:
    """What the best move achieves, and why the move actually played in
    your game went wrong."""
    before = chess.Board(fen)
    me = before.turn
    best_san = before.san(best)
    sentences = []

    after_best = before.copy(stack=False)
    after_best.push(best)
    features = _move_features(before, best)
    pv, score = _engine_line(engine, after_best)
    mate = _mate_for(score, me)
    change = _line_outcome(before, [best] + pv, me) if pv else _line_outcome(before, [best], me)
    line = _line(before, [best] + pv, 5)
    what = f"{best_san} {', '.join(features)}" if features else best_san
    if mate:
        sentences.append(f"{what}, and it forces mate: {line}.")
    elif change >= 1:
        sentences.append(f"{what}. The key line {line} wins {_material_words(change)}.")
    else:
        scope = _scope_notes(before, after_best, me, best.to_square)
        detail = f" — {scope[0]}" if scope else ""
        sentences.append(f"{what}{detail}. Main line: {line}.")

    if game_move is not None and game_move != best and game_move in before.legal_moves:
        after_game = before.copy(stack=False)
        after_game.push(game_move)
        game_san = before.san(game_move)
        gpv, gscore = _engine_line(engine, after_game)
        gchange = _line_outcome(after_game, gpv, me) if gpv else 0
        opp = _side(not me)
        if _mate_for(gscore, not me):
            sentences.append(f"Your game move {game_san} allowed a forced mate: {_line(after_game, gpv, 4)}.")
        elif gpv and gchange <= -1:
            sentences.append(f"In the game, {game_san} ran into {_line_noted(after_game, gpv, 4, losing=True)}, "
                             f"losing {_material_words(-gchange)}.")
        else:
            notes = _newly_hanging(before, after_game, me) or []
            why = [f"left your {notes[0]} undefended"] if notes else []
            why += [n for n in [_king_weakening(before, game_move, me)] if n]
            why += _scope_notes(before, after_game, me, game_move.to_square)
            if why:
                sentences.append(f"In the game, {game_san} " + why[0].replace("it ", "", 1) + ".")
            elif gpv:
                cp = gscore.pov(me).score(mate_score=MATE_CP) if gscore else None
                evaluation = f", leaving you at {cp / 100:+.1f}" if cp is not None else ""
                sentences.append(f"In the game, {game_san} let {opp} play {_line(after_game, gpv, 3)}{evaluation}.")
    return " ".join(sentences[:3])
