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
import explorer  # noqa: E402
import opening_importer  # noqa: E402
import progression  # noqa: E402
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

    def test_restart_replays_the_same_line(self):
        s = OpeningDrillSession(self.conn, "QG", PlayerColor.WHITE)
        s.start()
        line = [n.id for n in s.target_path]
        while not s.is_finished():
            s.reveal_and_advance()
            s.play_opponent_move()
        s.restart()
        self.assertEqual(([n.id for n in s.target_path], s.path_index, len(s.board.move_stack)), (line, 0, 0))

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


def add_attempt(conn, item_id, correct, outcome, when, item_type="puzzle", sharp=0):
    conn.execute("INSERT INTO attempts (item_type, item_id, correct, attempted_at, outcome, sharp) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (item_type, item_id, int(correct), when, outcome, sharp))


class ProgressionTests(unittest.TestCase):
    def test_level_curve_starts_10_25_100_then_grows(self):
        self.assertEqual(progression.LEVEL_COSTS[:3], [10, 25, 100])
        later = progression.LEVEL_COSTS[3:]
        self.assertTrue(all(b > a for a, b in zip(later, later[1:])))
        self.assertEqual(len(progression.LEVEL_COSTS), len(progression.LEVEL_NAMES) - 1)

    def test_level_for_boundaries(self):
        self.assertEqual(progression.level_for(0).name, "Pawn I")
        self.assertEqual(progression.level_for(9).name, "Pawn I")
        lvl = progression.level_for(10)
        self.assertEqual((lvl.name, lvl.xp_into_level, lvl.xp_for_level), ("Pawn II", 0, 25))
        self.assertEqual(progression.level_for(35).name, "Pawn III")
        top = progression.level_for(10 ** 6)
        self.assertEqual((top.name, top.xp_for_level, top.progress), ("King III", None, 1.0))

    def test_xp_by_outcome(self):
        conn = fresh_conn()
        add_attempt(conn, "a", True, "correct", "2026-10-01T10:00:00")
        add_attempt(conn, "b", False, "near_best", "2026-10-01T10:01:00")
        add_attempt(conn, "c", False, "revealed", "2026-10-01T10:02:00")
        add_attempt(conn, "d", False, None, "2026-10-01T10:03:00")       # old row: plain miss
        add_attempt(conn, "L", True, None, "2026-10-01T10:04:00", item_type="line")
        self.assertEqual(progression.total_xp(conn), 10 + 5 + 0 + 2 + 15)

    def test_sharp_bonus_only_for_correct(self):
        conn = fresh_conn()
        add_attempt(conn, "a", True, "correct", "2026-10-01T10:00:00", sharp=1)
        add_attempt(conn, "b", False, "near_best", "2026-10-01T10:01:00", sharp=1)
        add_attempt(conn, "c", False, "missed", "2026-10-01T10:02:00", sharp=1)
        self.assertEqual(progression.total_xp(conn), 10 + progression.SHARP_BONUS_XP + 5 + 2)

    def test_same_puzzle_pays_once_per_day(self):
        conn = fresh_conn()
        add_attempt(conn, "a", True, "correct", "2026-10-01T10:00:00")
        add_attempt(conn, "a", True, "correct", "2026-10-01T11:00:00")
        self.assertEqual(progression.total_xp(conn), 10)
        add_attempt(conn, "a", True, "correct", "2026-10-02T09:00:00")
        self.assertEqual(progression.total_xp(conn), 20)

    def test_clearing_a_weak_spot_pays_bonus(self):
        conn = fresh_conn()
        add_attempt(conn, "a", False, "missed", "2026-10-01T10:00:00")
        add_attempt(conn, "a", True, "correct", "2026-10-02T10:00:00")
        add_attempt(conn, "a", True, "correct", "2026-10-03T10:00:00")   # cleared here
        self.assertEqual(progression.total_xp(conn), 2 + 10 + 10 + progression.WEAK_SPOT_CLEARED_XP)
        self.assertNotIn("a", db.weak_item_ids(conn, "puzzle"))

    def test_puzzle_records_and_outcome_upgrade(self):
        conn = fresh_conn()
        db.record_attempt(conn, "puzzle", "a", True)
        row = db.record_attempt(conn, "puzzle", "a", False)
        db.record_attempt(conn, "puzzle", "a", False, "revealed")
        self.assertEqual(db.puzzle_records(conn)["a"], {"correct": 1, "near_best": 0, "missed": 2})
        db.set_attempt_outcome(conn, row, "near_best")
        self.assertEqual(db.puzzle_records(conn)["a"], {"correct": 1, "near_best": 1, "missed": 1})


BLACK_REP = """[Event "Sicilian"]

1. e4 c5 2. Nf3 d6 *

[Event "QGD"]

1. d4 d5 2. c4 e6 *
"""


def fake_stats(table):
    """stats_for / move_share stand-ins from {position_key: {uci: games}}."""
    def stats_for(fen):
        moves = table.get(repertoire_gaps.position_key(fen))
        if moves is None:
            return None
        ms = [explorer.MoveStat(u, u, n, n // 2, 0, n - n // 2) for u, n in moves.items()]
        return explorer.PositionStats(sum(moves.values()), ms)
    return stats_for


def key_after(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return repertoire_gaps.position_key(board.fen())


class LichessExplorerTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        opening_importer.import_pgn_text(BLACK_REP, "Black", PlayerColor.BLACK, self.conn)
        self.table = {
            key_after(): {"e2e4": 700, "d2d4": 250, "c2c4": 50},
            key_after("e4", "c5"): {"g1f3": 800, "b1c3": 200},
            key_after("d4", "d5"): {"c2c4": 900, "g1f3": 100},
        }

    def test_drill_lines_weighted_by_real_world_frequency(self):
        stats_for = fake_stats(self.table)

        def share(fen, uci):
            st = stats_for(fen)
            return st.share(uci) if st else None

        import random
        random.seed(1)
        counts = {"e4": 0, "d4": 0}
        for _ in range(600):
            s = OpeningDrillSession(self.conn, "Black", PlayerColor.BLACK, move_share=share)
            s.start()
            counts[s.target_path[0].move_san] += 1
        # separate lines: 1.e4 line ~ .7*.8 = .56, 1.d4 line ~ .25*.9 = .225  (≈ 71% vs 29%)
        self.assertGreater(counts["e4"], 2 * counts["d4"])
        self.assertGreater(counts["d4"], 60)

    def test_no_data_falls_back_to_uniform(self):
        s = OpeningDrillSession(self.conn, "Black", PlayerColor.BLACK, move_share=lambda fen, uci: None)
        self.assertTrue(s.start())

    def test_popular_gaps_are_unanswered_moves_ranked_by_frequency(self):
        gaps = repertoire_gaps.popular_gaps(self.conn, fake_stats(self.table))
        lines = [g.line for g in gaps]
        # 1.c4 is only 5% (under the 8% bar); 2.Nc3 in the Sicilian and 2.Nf3 in the QGD are holes
        self.assertEqual(lines, ["1. e4 c5 2. Nc3", "1. d4 d5 2. Nf3"])
        self.assertAlmostEqual(gaps[0].frequency, 0.7 * 0.2)
        self.assertTrue(all(g.color == "black" for g in gaps))

    def test_line_endings_are_not_gaps(self):
        # after 2...d6 the Sicilian line ends: no stats there should create gaps
        self.table[key_after("e4", "c5", "Nf3", "d6")] = {"d2d4": 900, "f1b5": 100}
        gaps = repertoire_gaps.popular_gaps(self.conn, fake_stats(self.table))
        self.assertFalse(any(g.prefix[:4] == ["e4", "c5", "Nf3", "d6"] for g in gaps))

    def test_castling_comes_back_in_standard_notation(self):
        fen = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
        stats = explorer._parse({"white": 5, "draws": 0, "black": 5,
                                 "moves": [{"uci": "e1h1", "san": "O-O", "white": 5, "draws": 0, "black": 5}]}, fen)
        self.assertEqual(stats.moves[0].uci, "e1g1")
        self.assertEqual(stats.share("e1g1"), 1.0)

    def test_rating_bands_around_your_level(self):
        self.assertEqual(explorer.rating_bands(self.conn), "1400,1600,1800")   # no games yet
        self.assertEqual(explorer.describe_bands("1600,1800"), "rated 1600–2000")


if __name__ == "__main__":
    unittest.main()
