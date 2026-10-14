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
import opening_tree  # noqa: E402
import progression  # noqa: E402
import spot  # noqa: E402
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


class OpeningTreeTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        opening_importer.import_pgn_text(QG_PGN, "QG", PlayerColor.WHITE, self.conn)

    def test_lines_merge_until_they_differ(self):
        tree = opening_tree.build(self.conn, "QG", PlayerColor.WHITE)
        self.assertEqual(len(tree.leaves), 2)
        trunk = [b for b in tree.branches if b.parent is None]
        self.assertEqual(len(trunk), 1)                       # 1.d4 d5 2.c4 shared
        self.assertEqual([n.move.move_san for n in trunk[0].nodes], ["d4", "d5", "c4"])
        self.assertEqual(sorted(c.nodes[0].move.move_san for c in trunk[0].children), ["c6", "e6"])
        self.assertEqual(trunk[0].lines, 2)

    def test_layout_puts_leaves_on_top_and_knots_over_their_leaves(self):
        tree = opening_tree.layout(opening_tree.build(self.conn, "QG", PlayerColor.WHITE), 400, 420)
        leaves = [b for b in tree.branches if not b.children]
        self.assertTrue(all(b.end.y == opening_tree.TOP for b in leaves))
        knot = next(b for b in tree.branches if b.parent is None).end
        self.assertAlmostEqual(knot.x, sum(b.end.x for b in leaves) / 2)
        self.assertGreater(tree.root.y, knot.y)               # grows upward from the root
        node = tree.node_for_path(["d2d4", "d7d5", "c2c4", "e7e6"])
        self.assertEqual(node.lines()[0][0], "QG - Orthodox")

    def test_opening_start_defaults_to_the_shared_trunk_and_can_move(self):
        tree = opening_tree.build(self.conn, "QG", PlayerColor.WHITE)
        start = opening_tree.start_node(self.conn, tree, "QG", PlayerColor.WHITE)
        self.assertEqual([m.move_san for m in start.path], ["d4", "d5", "c4"])   # where both lines agree
        early = tree.node_for_path(["d2d4"])
        self.assertFalse(opening_tree.is_inside(early, start))
        self.assertTrue(opening_tree.is_inside(tree.node_for_path(["d2d4", "d7d5", "c2c4", "e7e6"]), start))
        opening_tree.set_start(self.conn, "QG", PlayerColor.WHITE, early)          # move it earlier
        self.assertIs(opening_tree.start_node(self.conn, tree, "QG", PlayerColor.WHITE), early)
        opening_tree.set_start(self.conn, "QG", PlayerColor.WHITE, None)           # back to the default
        self.assertEqual(opening_tree.start_path(self.conn, "QG", PlayerColor.WHITE), ["d2d4", "d7d5", "c2c4"])

    def test_gaps_before_the_opening_starts_are_marked(self):
        gaps = [repertoire_gaps.PopularGap("white", ["d4"], "Nf6", .5, 1, 100, .5),
                repertoire_gaps.PopularGap("white", ["d4", "d5", "c4"], "dxc4", .2, .5, 100, .5)]
        repertoire_gaps.mark_inside(gaps, {"white": [["d2d4", "d7d5", "c2c4"]]})
        self.assertEqual([g.inside for g in gaps], [False, True])

    def test_many_lines_make_the_tree_wider_than_the_view(self):
        pgn = "".join(f'[Event "L{i}"]\n\n1. e4 {m} *\n\n' for i, m in
                      enumerate(["e5", "c5", "e6", "c6", "d5", "d6", "Nf6", "g6", "b6", "a6", "Nc6", "b5"]))
        opening_importer.import_pgn_text(pgn, "Wide", PlayerColor.WHITE, self.conn)
        tree = opening_tree.layout(opening_tree.build(self.conn, "Wide", PlayerColor.WHITE), 300, 420)
        self.assertEqual(len(tree.leaves), 12)
        self.assertGreater(tree.width, 300)


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


class FakeEngine:
    """Stockfish stand-in: analyse() answers from a {fen-prefix: [(pv ucis, cp or ('mate', n))]} table."""
    def __init__(self, table):
        self.table = table

    def analyse(self, board, limit, multipv=None):
        lines = []
        for ucis, score in self.table[board.fen().split(" ")[0]]:
            pov = chess.engine.Mate(score[1]) if isinstance(score, tuple) else chess.engine.Cp(score)
            lines.append({"pv": [chess.Move.from_uci(u) for u in ucis],
                          "score": chess.engine.PovScore(pov, board.turn)})
        return lines if multipv else lines[0]


class SpotTheTacticTests(unittest.TestCase):
    # White to move, Black's queen on d5 can be taken by the knight on c3
    HANGING = "rnb1kbnr/ppp1pppp/8/3q4/8/2N5/PPPP1PPP/R1BQKBNR w KQkq - 0 3"

    def test_material_win_is_a_tactic_and_counted_after_the_reply(self):
        board = chess.Board(self.HANGING)
        eng = FakeEngine({board.fen().split()[0]: [(["c3d5", "c7c6", "d5c3"], 900), (["d2d4"], -400)]})
        v = spot.classify(eng, board)
        self.assertEqual((v.is_tactic, v.reason, v.gain), (True, "material", 9))
        self.assertIn("forcing sequence that wins a queen", v.describe(board))

    def test_mate_quiet_and_sharp_but_not_tactical(self):
        board = chess.Board()
        key = board.fen().split()[0]
        self.assertEqual(spot.classify(FakeEngine({key: [(["e2e4"], ("mate", 3)), (["d2d4"], 0)]}), board).reason, "mate")
        self.assertEqual(spot.classify(FakeEngine({key: [(["e2e4"], ("mate", 9)), (["d2d4"], 0)]}), board).reason,
                         "unclear")                                     # too long to count as a tactic
        # only one good move, but nothing forcing: not a tactic, and not quiet either -> skipped
        self.assertEqual(spot.classify(FakeEngine({key: [(["e2e4"], 150), (["d2d4"], 0)]}), board).reason, "unclear")
        quiet = spot.classify(FakeEngine({key: [(["e2e4"], 30), (["d2d4"], 20)]}), board)
        self.assertEqual((quiet.is_tactic, quiet.reason), (False, "quiet"))

    def test_material_won_by_a_quiet_move_is_not_a_tactic(self):
        # same hanging queen, but the line starts with a quiet move (a2a3) before taking it
        board = chess.Board(self.HANGING)
        pv = ["a2a3", "a7a6", "c3d5", "c7c6"]
        self.assertEqual(spot.forcing_gain(board, [chess.Move.from_uci(u) for u in pv]), 0)
        self.assertEqual(spot.forcing_gain(board, [chess.Move.from_uci(u) for u in ["c3d5", "c7c6"]]), 9)

    def test_winning_back_what_was_just_taken_is_not_a_tactic(self):
        board = chess.Board(self.HANGING)
        line = [chess.Move.from_uci(u) for u in ["c3d5", "c7c6"]]
        self.assertEqual(spot.forcing_gain(board, line, just_lost=0), 9)
        self.assertEqual(spot.forcing_gain(board, line, just_lost=9), 0)     # they'd just taken your queen
        self.assertEqual(spot.forcing_gain(board, line, just_lost=3), 6)     # ...or a knight: still +6

    def test_quiet_positions_start_after_five_moves_each(self):
        self.assertFalse(spot.usable_as_quiet(spot.Candidate("g", 9, START, "e2e4")))
        self.assertTrue(spot.usable_as_quiet(spot.Candidate("g", 10, START, "e2e4")))
        conn = fresh_conn()
        conn.execute("INSERT INTO games (id, chess_com_url, pgn, played_at, time_control, my_color, "
                     "opponent_username, result) VALUES ('g', 'u', '', '2026-01-01', '600', 'white', 'x', 'win')")
        for ply in (2, 8, 12):
            conn.execute("INSERT INTO move_evaluations (game_id, ply, fen_before, move_uci, best_move_uci, "
                         "cp_before, cp_after, depth, second_move_uci, cp_second, third_move_uci, cp_third) "
                         "VALUES ('g', ?, ?, 'e2e4', 'e2e4', 20, 20, 14, 'd2d4', 15, 'c2c4', 10)", (ply, f"fen{ply}"))
        self.assertEqual([c.ply for c in spot.candidates(conn, True, set())], [12])

    def test_solution_stops_at_the_winning_move(self):
        board = chess.Board(self.HANGING)
        pv = [chess.Move.from_uci(u) for u in ["c3d5", "c7c6", "d5c3", "e7e5"]]
        score = chess.engine.PovScore(chess.engine.Cp(900), chess.WHITE)
        # the queen is won by the first move; the line ends there, with your move
        self.assertEqual(spot.solution_for(board, pv, score), pv[:1])
        self.assertEqual(spot.forcing_win(board, pv), (9, 1))

    def test_multi_move_solution_and_mate_line(self):
        # 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6?? 4.Qxf7# — a mate in one from the position before it
        board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
        pv = [chess.Move.from_uci("h5f7")]
        mate = chess.engine.PovScore(chess.engine.Mate(1), chess.WHITE)
        self.assertEqual(spot.solution_for(board, pv, mate), pv)
        self.assertEqual(spot.mate_line(board, pv + [chess.Move.from_uci("a7a6")]), pv)   # stops at mate
        # Qa4 attacks the undefended queen (a threat), the king steps aside, Qxb5: two of your moves
        fork = chess.Board("4k3/8/8/1q6/8/8/8/3QK3 w - - 0 1")
        line = [chess.Move.from_uci(u) for u in ["d1a4", "e8e7", "a4b5"]]
        cp = chess.engine.PovScore(chess.engine.Cp(900), chess.WHITE)
        sol = spot.solution_for(fork, line + [chess.Move.from_uci("e7e6")], cp)
        self.assertEqual(sol, line)

    def test_forcing_moves_checks_captures_threats(self):
        board = chess.Board()
        self.assertFalse(spot.is_forcing(board, chess.Move.from_uci("e2e4")))
        # Bb5 attacks the knight on c6? no - the knight is defended and worth the same: not a threat
        ruy = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
        self.assertFalse(spot.is_forcing(ruy, chess.Move.from_uci("f1b5")))
        # d4-d5 attacks the knight on c6 with a pawn: a new threat (knight worth more than the pawn)
        push = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/3PP3/8/PPP2PPP/RNBQKBNR w KQkq - 1 3")
        self.assertTrue(spot.is_forcing(push, chess.Move.from_uci("d4d5")))
        # attacking a defended pawn of equal value isn't a threat; a capture always is forcing
        four = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/4P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 4 4")
        self.assertFalse(spot.is_forcing(four, chess.Move.from_uci("d2d4")))
        self.assertTrue(spot.is_forcing(four, chess.Move.from_uci("f3e5")))

    def test_trivial_recapture_and_check_are_skipped(self):
        # 1.e4 d5 2.exd5 — Black recaptures on d5 with the queen
        board = chess.Board("rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2")
        self.assertTrue(spot.is_trivial(board, chess.Move.from_uci("d8d5"), chess.Move.from_uci("e4d5"), 1))
        self.assertFalse(spot.is_trivial(board, chess.Move.from_uci("g8f6"), chess.Move.from_uci("e4d5"), 1))
        in_check = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/5PPq/8/PPPPP2P/RNBQKBNR w KQkq - 1 3")
        self.assertTrue(spot.is_trivial(in_check, chess.Move.from_uci("g2g3"), None, 0))

    def test_judging_moves(self):
        board = chess.Board(self.HANGING)
        v = spot.Verdict(True, "material", chess.Move.from_uci("c3d5"), 900, -400)
        self.assertEqual(spot.judge_move(None, board, v, chess.Move.from_uci("c3d5")), (True, 0))
        after = board.copy(); after.push(chess.Move.from_uci("d2d4"))
        eng = FakeEngine({after.fen().split()[0]: [([], 400)]})       # Black to move, +4 for Black
        self.assertEqual(spot.judge_move(eng, board, v, chess.Move.from_uci("d2d4")), (False, 1300))

    def test_tactic_needs_the_best_move_quiet_allows_half_a_pawn(self):
        board = chess.Board()
        after = board.copy(); after.push(chess.Move.from_uci("d2d4"))
        eng = FakeEngine({after.fen().split()[0]: [([], -130)]})      # Black to move: White +1.30
        tactic = spot.Verdict(True, "only_move", chess.Move.from_uci("e2e4"), 150, 0)
        quiet = spot.Verdict(False, "quiet", chess.Move.from_uci("e2e4"), 150, 140)
        self.assertEqual(spot.judge_move(eng, board, tactic, chess.Move.from_uci("d2d4")), (False, 20))
        self.assertEqual(spot.judge_move(eng, board, quiet, chess.Move.from_uci("d2d4")), (True, 20))

    def test_scoring_and_summary(self):
        conn = fresh_conn()
        tactic = spot.Verdict(True, "only_move", chess.Move.from_uci("e2e4"), 150, 0)
        quiet = spot.Verdict(False, "quiet", chess.Move.from_uci("e2e4"), 20, 10)
        spot.record(conn, "f1", tactic, True, True)     # spotted, good move
        spot.record(conn, "f2", quiet, True, True)      # false alarm, good move
        spot.record(conn, "f3", tactic, False, False)   # missed it, bad move
        s = spot.summary(conn)
        self.assertEqual((s.rounds, s.spotted, s.tactics, s.false_alarms, s.quiet, s.moves_ok), (3, 1, 2, 1, 1, 2))
        self.assertEqual(spot.xp(conn), 1 * spot.CALL_XP + 2 * spot.MOVE_XP)
        self.assertEqual(progression.total_xp(conn), spot.xp(conn))


if __name__ == "__main__":
    unittest.main()
