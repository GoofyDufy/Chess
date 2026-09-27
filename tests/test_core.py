"""Core logic tests — run with:  python -m unittest discover tests

Each test uses a fresh temporary SQLite database, never chessprep.db.
No Stockfish needed: engine output is supplied as MoveEvaluation records.
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyzer  # noqa: E402
import db  # noqa: E402
import opening_importer  # noqa: E402
import repertoire_gaps  # noqa: E402
from drill import OpeningDrillSession  # noqa: E402
from models import Game, GameResult, MoveEvaluation, PlayerColor, time_class  # noqa: E402
from stats import Record  # noqa: E402

import chess  # noqa: E402

START = chess.STARTING_FEN


def fresh_conn():
    db.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"
    return db.get_connection()


def ev(ply, fen, move, best, before, after, second=None):
    return MoveEvaluation(game_id="g1", ply=ply, fen_before=fen, move_uci=move, best_move_uci=best,
                          cp_before=before, cp_after=after, depth=14, cp_second=second)


class PuzzleGenerationTests(unittest.TestCase):
    def test_threshold_filters_cached_evaluations(self):
        evals = [ev(0, START, "e2e4", "d2d4", 30, -200)]     # 2.3-pawn swing
        self.assertEqual(len(analyzer.puzzles_from_evaluations(evals, 150)), 1)
        self.assertEqual(len(analyzer.puzzles_from_evaluations(evals, 300)), 0)

    def test_best_move_played_is_never_a_puzzle(self):
        evals = [ev(0, START, "e2e4", "e2e4", 30, -500)]
        self.assertEqual(analyzer.puzzles_from_evaluations(evals, 100), [])

    def test_decisive_position_skipped(self):
        # cp_after None = second engine call skipped (already decisive)
        evals = [ev(0, START, "e2e4", "d2d4", 800, None)]
        self.assertEqual(analyzer.puzzles_from_evaluations(evals, 100), [])

    def test_puzzle_fields(self):
        p = analyzer.puzzles_from_evaluations([ev(0, START, "e2e4", "d2d4", 30, -200)], 150)[0]
        self.assertEqual((p.correct_move_san, p.played_move_san, p.eval_swing_cp), ("d4", "e4", 230))


QG_PGN = """[Event "QG - Orthodox"]

1. d4 d5 2. c4 e6 3. Nc3 {note on Nc3} Nf6 *

[Event "QG - Slav"]

1. d4 d5 2. c4 c6 3. Nf3 Nf6 *
"""

OTHER_PGN = """[Event "Other - QGD Exchange"]

1. d4 d5 2. c4 e6 3. cxd5 exd5 *
"""


class OpeningImportAndDrillTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        opening_importer.import_pgn_text(QG_PGN, "QG", PlayerColor.WHITE, self.conn)

    def test_reimport_updates_notes_without_duplicates(self):
        before = self.conn.execute("SELECT COUNT(*) FROM opening_lines").fetchone()[0]
        added = opening_importer.import_pgn_text(QG_PGN.replace("note on Nc3", "new note"), "QG",
                                                 PlayerColor.WHITE, self.conn)
        after = self.conn.execute("SELECT COUNT(*) FROM opening_lines").fetchone()[0]
        self.assertEqual((added, before), (0, after))
        notes = self.conn.execute("SELECT notes FROM opening_lines WHERE move_uci='b1c3'").fetchone()[0]
        self.assertEqual(notes, "new note")

    def test_drill_stays_on_the_chosen_line_and_is_strict(self):
        slav = next(r for r in db.root_lines(self.conn, "QG", "white") if r.line_name == "QG - Slav")
        s = OpeningDrillSession(self.conn, "QG", PlayerColor.WHITE)
        self.assertTrue(s.start(slav.id))
        self.assertTrue(s.attempt_my_move(chess.Move.from_uci("d2d4")).correct)
        s.play_opponent_move()
        self.assertTrue(s.attempt_my_move(chess.Move.from_uci("c2c4")).correct)
        self.assertEqual(s.play_opponent_move(), "c6")          # never drifts onto the Orthodox line
        wrong = s.attempt_my_move(chess.Move.from_uci("b1c3"))  # book in the OTHER line, not this one
        self.assertFalse(wrong.correct)
        self.assertEqual(len(s.board.move_stack), 4)            # nothing pushed on a miss

    def test_book_elsewhere_is_informational_across_separate_imports(self):
        opening_importer.import_pgn_text(OTHER_PGN, "QG", PlayerColor.WHITE, self.conn)
        orthodox = next(r for r in db.root_lines(self.conn, "QG", "white") if r.line_name == "QG - Orthodox")
        s = OpeningDrillSession(self.conn, "QG", PlayerColor.WHITE)
        s.start(orthodox.id)
        for uci in ("d2d4", None, "c2c4", None):
            s.attempt_my_move(chess.Move.from_uci(uci)) if uci else s.play_opponent_move()
        result = s.attempt_my_move(chess.Move.from_uci("c4d5"))   # the Exchange line's move
        self.assertFalse(result.correct)
        self.assertTrue(result.in_book)

    def test_move_note_after_reveal(self):
        orthodox = next(r for r in db.root_lines(self.conn, "QG", "white") if r.line_name == "QG - Orthodox")
        s = OpeningDrillSession(self.conn, "QG", PlayerColor.WHITE)
        s.start(orthodox.id)
        for _ in range(2):
            s.reveal_and_advance()
            s.play_opponent_move()
        self.assertEqual(s.reveal_and_advance(), "Nc3")
        self.assertEqual(s.last_move_note(), "note on Nc3")


class RepertoireGapTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        opening_importer.import_pgn_text(QG_PGN, "QG", PlayerColor.WHITE, self.conn)

    def add_game(self, moves, result=GameResult.WIN, url="u"):
        pgn = f'[Event "x"]\n[WhiteElo "1650"]\n\n{moves} *\n'
        db.save_game(self.conn, Game(chess_com_url=url, pgn=pgn, played_at=dt.datetime(2026, 9, 1),
                                     time_control="900+10", my_color=PlayerColor.WHITE,
                                     opponent_username="opp", result=result))

    def test_opponent_deviation_is_a_gap(self):
        for i in range(3):
            self.add_game("1. d4 Nf6 2. c4 e6", url=f"a{i}")
        report = repertoire_gaps.compute(self.conn)
        gap = report.gaps[0]
        self.assertEqual((gap.prefix, gap.move, gap.by_opponent, gap.record.games), (["d4"], "Nf6", True, 3))

    def test_game_inside_repertoire_has_no_early_gap(self):
        for i in range(2):
            self.add_game("1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5", url=f"b{i}")
        gaps = repertoire_gaps.compute(self.conn).gaps
        self.assertTrue(all(len(g.prefix) >= 6 for g in gaps))


class WeakSpotTests(unittest.TestCase):
    def test_weak_until_solved_twice(self):
        conn = fresh_conn()
        db.record_attempt(conn, "puzzle", "p1", False)
        self.assertIn("p1", db.weak_item_ids(conn, "puzzle"))
        db.record_attempt(conn, "puzzle", "p1", True)
        self.assertIn("p1", db.weak_item_ids(conn, "puzzle"))      # one clean solve isn't enough
        db.record_attempt(conn, "puzzle", "p1", True)
        self.assertNotIn("p1", db.weak_item_ids(conn, "puzzle"))
        db.record_attempt(conn, "puzzle", "p2", True)
        self.assertNotIn("p2", db.weak_item_ids(conn, "puzzle"))   # never missed = never weak


class SmallHelperTests(unittest.TestCase):
    def test_time_class_buckets(self):
        self.assertEqual([time_class(t) for t in ("60", "180+2", "600", "900+10", "1/86400")],
                         ["Bullet", "Blitz", "Rapid", "Rapid", "Daily"])

    def test_record_score(self):
        r = Record()
        for res in ("win", "draw", "loss", "win"):
            r.add(res)
        self.assertAlmostEqual(r.score, 0.625)


if __name__ == "__main__":
    unittest.main()
