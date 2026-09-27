"""
Runs each of your games through Stockfish and turns significant mistakes
into puzzles: the position right before the bad move, your move, and the
engine's preferred move.

Two phases, deliberately kept separate:

  1. evaluate_games_batch() — the slow part. Runs Stockfish once per move
     of yours and returns raw MoveEvaluation records with NO threshold
     applied. The caller saves these permanently (move_evaluations table),
     so a game never goes through the engine twice.
  2. puzzles_from_evaluations() — pure and engine-free. Turns cached
     evaluations into puzzles at whatever threshold is currently set, so
     changing the threshold is instant.

Don't merge threshold checking back into the engine loop — that would
mean every threshold change requires re-running Stockfish on everything.
"""

from __future__ import annotations

import io
from typing import Callable, List, Optional

import chess
import chess.engine
import chess.pgn

from models import Game, MoveEvaluation, PlayerColor, Puzzle, PuzzleSource

# Centipawn drop that counts as "worth turning into a puzzle".
# Tune this: lower = more puzzles (including minor inaccuracies),
# higher = only real blunders.
MISTAKE_THRESHOLD_CP = 150

# How deep Stockfish searches each position. Higher = more accurate but slower.
ANALYSIS_DEPTH = 14

# If the position was ALREADY at or beyond this evaluation (either
# direction) before your move, the game was already decisively won or
# lost — skip it, since further mistakes there are less instructive and
# it also saves the second engine call (no need to check what you played).
DECISIVE_EVAL_CP = 600

# Mate scores are clamped to +/- this value so comparisons still work.
MATE_CP = 10000

# How many top engine moves to record per position (best + 2nd + 3rd).
MULTI_PV = 3

# Bump when the stored evaluation data changes shape; games cached under
# an older version get re-analyzed once (see db.games_needing_evaluation).
# 1 = best move only, 2 = MultiPV top 3.
EVAL_VERSION = 2


def _score_to_cp(score: chess.engine.PovScore, pov_color: chess.Color) -> int:
    """Convert an engine score to centipawns from the given color's POV,
    clamping mate scores to a large finite value so comparisons still work."""
    pov = score.pov(pov_color)
    if pov.is_mate():
        mate_in = pov.mate()
        return MATE_CP if mate_in and mate_in > 0 else -MATE_CP
    return pov.score()


# Stockfish itself has no concept of tactical "types" — it only gives an
# evaluation and a best move. Everything below is our own lightweight
# heuristic classifier layered on top, similar in spirit to (but far
# simpler than) how sites like Lichess tag puzzle themes.
_PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}


def _classify_puzzle_type(
    board_before: chess.Board, move: chess.Move, my_color: chess.Color,
    cp_before: int, swing: int,
) -> str:
    # you had a forced mate available and didn't take it (cp_before hits
    # the mate clamp only when the engine reported a mate for you)
    if cp_before >= MATE_CP:
        return "Missed Mate"

    # did your move leave a piece attacked with no defender? (a simple
    # "is anyone attacking this square, is anyone defending it" check —
    # not a full exchange evaluation, so it can miss subtler cases)
    board_after = board_before.copy()
    board_after.push(move)
    opponent = not my_color
    worst_hanging_value = 0
    for square in chess.SQUARES:
        piece = board_after.piece_at(square)
        if piece and piece.color == my_color:
            if board_after.attackers(opponent, square) and not board_after.attackers(my_color, square):
                worst_hanging_value = max(worst_hanging_value, _PIECE_VALUES.get(piece.piece_type, 0))
    if worst_hanging_value >= 3:  # minor piece or better left hanging
        return "Hanging Piece"

    # fallback: classify by severity alone, same tiers chess.com/lichess
    # use for move-quality labels
    if swing >= 300:
        return "Blunder"
    elif swing >= 150:
        return "Mistake"
    else:
        return "Inaccuracy"


def _my_chess_color(game: Game) -> chess.Color:
    return chess.WHITE if game.my_color == PlayerColor.WHITE else chess.BLACK


def count_my_moves(game: Game) -> int:
    """How many moves in this game were played by you — no engine needed,
    just PGN walking. Used to size the progress bar before analysis starts."""
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    if parsed is None:
        return 0
    my_color = _my_chess_color(game)
    board = parsed.board()
    count = 0
    for move in parsed.mainline_moves():
        if board.turn == my_color:
            count += 1
        board.push(move)
    return count


class AnalysisStopped(Exception):
    """Raised inside the engine loop when the user presses Stop. Games
    already finished (and saved via on_game_done) are kept."""


def evaluate_game(
    game: Game, engine: chess.engine.SimpleEngine,
    on_move_analyzed: Optional[Callable[[], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[MoveEvaluation]:
    """Runs Stockfish on every move of yours in one game and returns the
    raw results — no threshold applied, nothing filtered out."""
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    if parsed is None:
        return []

    my_color = _my_chess_color(game)
    limit = chess.engine.Limit(depth=ANALYSIS_DEPTH)
    evaluations: List[MoveEvaluation] = []
    board = parsed.board()

    for ply, move in enumerate(parsed.mainline_moves()):
        if board.turn == my_color:
            if should_stop and should_stop():
                raise AnalysisStopped()

            # MultiPV: one search returns the top 3 lines, best first
            lines = engine.analyse(board, limit, multipv=MULTI_PV)
            info_before = lines[0]
            best_move = info_before["pv"][0] if info_before.get("pv") else None
            cp_before = _score_to_cp(info_before["score"], my_color)
            alternatives = [
                (line["pv"][0].uci(), _score_to_cp(line["score"], my_color))
                for line in lines[1:] if line.get("pv")
            ]
            alternatives += [(None, None)] * (2 - len(alternatives))

            # already decisively won or lost before this move — skip the
            # second engine call entirely; this move can never become a
            # puzzle at any threshold
            cp_after: Optional[int] = None
            if abs(cp_before) < DECISIVE_EVAL_CP:
                board.push(move)
                info_after = engine.analyse(board, limit)
                cp_after = _score_to_cp(info_after["score"], my_color)
                board.pop()

            evaluations.append(MoveEvaluation(
                game_id=game.id, ply=ply, fen_before=board.fen(),
                move_uci=move.uci(),
                best_move_uci=best_move.uci() if best_move else None,
                cp_before=cp_before, cp_after=cp_after, depth=ANALYSIS_DEPTH,
                second_move_uci=alternatives[0][0], cp_second=alternatives[0][1],
                third_move_uci=alternatives[1][0], cp_third=alternatives[1][1],
            ))

            if on_move_analyzed:
                on_move_analyzed()

        board.push(move)

    return evaluations


def evaluate_games_batch(
    games: List[Game], engine_path: str,
    on_game_done: Callable[[Game, List[MoveEvaluation]], None],
    on_move_analyzed: Optional[Callable[[], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> int:
    """Evaluates many games reusing one Stockfish process. on_game_done
    is called as soon as each game finishes so the caller can save it
    immediately — if the run is stopped or crashes partway, every
    completed game is already cached. Returns how many games finished."""
    finished = 0
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        for game in games:
            evaluations = evaluate_game(game, engine, on_move_analyzed, should_stop)
            on_game_done(game, evaluations)
            finished += 1
    return finished


def puzzles_from_evaluations(
    evaluations: List[MoveEvaluation], threshold_cp: int = MISTAKE_THRESHOLD_CP,
) -> List[Puzzle]:
    """Pure, engine-free: turns cached evaluations into puzzles at the
    given threshold. Cheap enough to re-run over every game whenever the
    threshold changes."""
    puzzles: List[Puzzle] = []
    for ev in evaluations:
        if ev.cp_after is None or ev.best_move_uci is None:
            continue
        if ev.best_move_uci == ev.move_uci:
            continue
        swing = ev.cp_before - ev.cp_after
        if swing < threshold_cp:
            continue

        board = chess.Board(ev.fen_before)
        move = chess.Move.from_uci(ev.move_uci)
        best_move = chess.Move.from_uci(ev.best_move_uci)
        puzzles.append(Puzzle(
            fen=ev.fen_before,
            correct_move_san=board.san(best_move),
            correct_move_uci=ev.best_move_uci,
            source=PuzzleSource.OWN_GAME_MISTAKE,
            played_move_san=board.san(move),
            eval_swing_cp=swing,
            eval_before_cp=ev.cp_before,
            eval_after_cp=ev.cp_after,
            puzzle_type=_classify_puzzle_type(board, move, board.turn, ev.cp_before, swing),
            source_game_id=ev.game_id,
        ))
    return puzzles
