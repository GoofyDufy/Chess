"""SQLite storage for games, puzzles, and review state."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import (
    Game, GameResult, MoveEvaluation, OpeningLine, PlayerColor, Puzzle,
    PuzzleSource, ReviewState, in_time_filter,
)

from analyzer import EVAL_VERSION

DB_PATH = Path(__file__).parent / "chessprep.db"

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    chess_com_url TEXT UNIQUE,
    pgn TEXT,
    played_at TEXT,
    time_control TEXT,
    my_color TEXT,
    opponent_username TEXT,
    result TEXT,
    is_analyzed INTEGER DEFAULT 0,
    analyzed_at TEXT,
    evaluated_at TEXT
);

-- raw Stockfish output for every one of your moves, cached permanently
-- so no game is ever sent through the engine twice (see analyzer.py)
CREATE TABLE IF NOT EXISTS move_evaluations (
    game_id TEXT,
    ply INTEGER,
    fen_before TEXT,
    move_uci TEXT,
    best_move_uci TEXT,
    cp_before INTEGER,
    cp_after INTEGER,
    depth INTEGER,
    second_move_uci TEXT,
    cp_second INTEGER,
    third_move_uci TEXT,
    cp_third INTEGER,
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS puzzles (
    id TEXT PRIMARY KEY,
    fen TEXT,
    correct_move_san TEXT,
    correct_move_uci TEXT,
    source TEXT,
    played_move_san TEXT,
    eval_swing_cp INTEGER,
    eval_before_cp INTEGER,
    eval_after_cp INTEGER,
    puzzle_type TEXT,
    source_game_id TEXT,
    opening_line_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS opening_lines (
    id TEXT PRIMARY KEY,
    move_san TEXT,
    move_uci TEXT,
    fen_after_move TEXT,
    repertoire_name TEXT,
    my_color TEXT,
    move_number INTEGER,
    parent_id TEXT,
    is_line_end INTEGER DEFAULT 0,
    line_name TEXT,
    is_enabled INTEGER DEFAULT 1,
    fen_before_move TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS review_state (
    puzzle_id TEXT PRIMARY KEY,
    ease_factor REAL,
    interval_days INTEGER,
    repetitions INTEGER,
    due_date TEXT,
    last_reviewed_at TEXT,
    consecutive_correct INTEGER,
    consecutive_wrong INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


def get_connection() -> sqlite3.Connection:
    # check_same_thread=False: the GUI runs import/analyze on background
    # threads so the window doesn't freeze, and they share this same
    # connection. We don't do concurrent writes from multiple threads at
    # once, so this is safe here.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Adds columns introduced after someone's chessprep.db already
    existed. CREATE TABLE IF NOT EXISTS won't add new columns to an
    existing table, so we patch them in here (no-op if already present)."""
    game_cols = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
    if "evaluated_at" not in game_cols:
        # NULL for every existing game: games analyzed before the cache
        # existed keep their puzzles, but have no cached evaluations yet
        conn.execute("ALTER TABLE games ADD COLUMN evaluated_at TEXT")
    if "eval_version" not in game_cols:
        # games cached before versioning existed are version 1 (best move only)
        conn.execute("ALTER TABLE games ADD COLUMN eval_version INTEGER")
        conn.execute("UPDATE games SET eval_version = 1 WHERE evaluated_at IS NOT NULL")
    eval_cols = {row["name"] for row in conn.execute("PRAGMA table_info(move_evaluations)")}
    for col, col_type in (("second_move_uci", "TEXT"), ("cp_second", "INTEGER"),
                          ("third_move_uci", "TEXT"), ("cp_third", "INTEGER")):
        if col not in eval_cols:
            conn.execute(f"ALTER TABLE move_evaluations ADD COLUMN {col} {col_type}")
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(puzzles)")}
    if "eval_before_cp" not in existing_cols:
        conn.execute("ALTER TABLE puzzles ADD COLUMN eval_before_cp INTEGER")
    if "eval_after_cp" not in existing_cols:
        conn.execute("ALTER TABLE puzzles ADD COLUMN eval_after_cp INTEGER")
    if "puzzle_type" not in existing_cols:
        conn.execute("ALTER TABLE puzzles ADD COLUMN puzzle_type TEXT")
    opening_cols = {row["name"] for row in conn.execute("PRAGMA table_info(opening_lines)")}
    if "line_name" not in opening_cols:
        conn.execute("ALTER TABLE opening_lines ADD COLUMN line_name TEXT")
    if "is_enabled" not in opening_cols:
        conn.execute("ALTER TABLE opening_lines ADD COLUMN is_enabled INTEGER DEFAULT 1")
    if "fen_before_move" not in opening_cols:
        conn.execute("ALTER TABLE opening_lines ADD COLUMN fen_before_move TEXT")
        # backfill for lines imported before this column existed: a node's
        # fen_before_move is simply its parent's fen_after_move (or the
        # starting position for root nodes) — no chess replay needed,
        # every row's fen_after_move is already stored.
        conn.execute(
            """UPDATE opening_lines
               SET fen_before_move = (
                   SELECT parent.fen_after_move FROM opening_lines AS parent
                   WHERE parent.id = opening_lines.parent_id
               )
               WHERE fen_before_move IS NULL AND parent_id IS NOT NULL"""
        )
        conn.execute(
            """UPDATE opening_lines
               SET fen_before_move = ?
               WHERE fen_before_move IS NULL AND parent_id IS NULL""",
            (STARTING_FEN,),
        )
    conn.commit()


# ---------- Games ----------

def save_game(conn: sqlite3.Connection, game: Game) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO games
           (id, chess_com_url, pgn, played_at, time_control, my_color,
            opponent_username, result, is_analyzed, analyzed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            game.id, game.chess_com_url, game.pgn,
            game.played_at.isoformat(), game.time_control,
            game.my_color.value, game.opponent_username, game.result.value,
            int(game.is_analyzed),
            game.analyzed_at.isoformat() if game.analyzed_at else None,
        ),
    )
    conn.commit()


def game_exists(conn: sqlite3.Connection, chess_com_url: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM games WHERE chess_com_url = ?", (chess_com_url,)
    ).fetchone()
    return row is not None


def games_needing_evaluation(conn: sqlite3.Connection, limit: int,
                             time_classes: Optional[set] = None) -> List[Game]:
    """Games with no cached Stockfish evaluations, newest first, optionally
    limited to some time classes (e.g. Rapid only). Never-analyzed games
    come before ones analyzed under the old (pre-cache) system, since the
    latter already have puzzles."""
    # also re-queues games cached under an older EVAL_VERSION (e.g. before
    # MultiPV), so their stored data gets the newer fields
    rows = conn.execute(
        """SELECT * FROM games
           WHERE evaluated_at IS NULL OR COALESCE(eval_version, 1) < ?
           ORDER BY is_analyzed ASC, played_at DESC""",
        (EVAL_VERSION,),
    ).fetchall()
    matching = [r for r in rows if in_time_filter(r["time_control"], time_classes)]
    return [_row_to_game(r) for r in matching[:limit]]


def game_stats(conn: sqlite3.Connection, time_classes: Optional[set] = None) -> dict:
    """Counts for the games summary line in the Puzzle Review tab."""
    """cached = saved at the current EVAL_VERSION; legacy = analyzed but
    needing a one-time re-analysis (no saved data, or an older format);
    uncached = the subset of legacy with no saved data at all."""
    counts = {"total": 0, "cached": 0, "legacy": 0, "new": 0, "uncached": 0}
    for r in conn.execute("SELECT time_control, is_analyzed, evaluated_at, eval_version FROM games"):
        if not in_time_filter(r["time_control"], time_classes):
            continue
        counts["total"] += 1
        if r["evaluated_at"] is not None and (r["eval_version"] or 1) >= EVAL_VERSION:
            counts["cached"] += 1
        elif r["is_analyzed"]:
            counts["legacy"] += 1
            counts["uncached"] += r["evaluated_at"] is None
        else:
            counts["new"] += 1
    return counts


def _row_to_game(r: sqlite3.Row) -> Game:
    return Game(
        id=r["id"], chess_com_url=r["chess_com_url"], pgn=r["pgn"],
        played_at=dt.datetime.fromisoformat(r["played_at"]),
        time_control=r["time_control"],
        my_color=PlayerColor(r["my_color"]),
        opponent_username=r["opponent_username"],
        result=GameResult(r["result"]),
        is_analyzed=bool(r["is_analyzed"]),
        analyzed_at=dt.datetime.fromisoformat(r["analyzed_at"]) if r["analyzed_at"] else None,
    )


def get_game(conn: sqlite3.Connection, game_id: str) -> Optional[Game]:
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    return _row_to_game(row) if row else None


# ---------- Move evaluations (Stockfish cache) ----------

def save_game_evaluations(conn: sqlite3.Connection, game_id: str,
                          evaluations: List[MoveEvaluation]) -> None:
    """Stores one game's engine output and marks it evaluated, in a
    single transaction — a game is either fully cached or not at all."""
    now = dt.datetime.now().isoformat()
    with conn:
        conn.execute("DELETE FROM move_evaluations WHERE game_id = ?", (game_id,))
        conn.executemany(
            """INSERT INTO move_evaluations
               (game_id, ply, fen_before, move_uci, best_move_uci,
                cp_before, cp_after, depth, second_move_uci, cp_second,
                third_move_uci, cp_third)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (e.game_id, e.ply, e.fen_before, e.move_uci, e.best_move_uci,
                 e.cp_before, e.cp_after, e.depth, e.second_move_uci, e.cp_second,
                 e.third_move_uci, e.cp_third)
                for e in evaluations
            ],
        )
        conn.execute(
            """UPDATE games SET evaluated_at = ?, eval_version = ?, is_analyzed = 1,
               analyzed_at = ? WHERE id = ?""",
            (now, EVAL_VERSION, now, game_id),
        )


def all_evaluations_by_game(conn: sqlite3.Connection) -> Dict[str, List[MoveEvaluation]]:
    rows = conn.execute("SELECT * FROM move_evaluations ORDER BY game_id, ply").fetchall()
    by_game: Dict[str, List[MoveEvaluation]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(MoveEvaluation(
            game_id=r["game_id"], ply=r["ply"], fen_before=r["fen_before"],
            move_uci=r["move_uci"], best_move_uci=r["best_move_uci"],
            cp_before=r["cp_before"], cp_after=r["cp_after"], depth=r["depth"],
            second_move_uci=r["second_move_uci"], cp_second=r["cp_second"],
            third_move_uci=r["third_move_uci"], cp_third=r["cp_third"],
        ))
    return by_game


# ---------- Puzzles ----------

def _insert_puzzle(conn: sqlite3.Connection, puzzle: Puzzle) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO puzzles
           (id, fen, correct_move_san, correct_move_uci, source,
            played_move_san, eval_swing_cp, eval_before_cp, eval_after_cp,
            puzzle_type, source_game_id, opening_line_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            puzzle.id, puzzle.fen, puzzle.correct_move_san,
            puzzle.correct_move_uci, puzzle.source.value,
            puzzle.played_move_san, puzzle.eval_swing_cp,
            puzzle.eval_before_cp, puzzle.eval_after_cp, puzzle.puzzle_type,
            puzzle.source_game_id, puzzle.opening_line_id,
            puzzle.created_at.isoformat(),
        ),
    )
    # every new puzzle gets a fresh review state, due immediately
    conn.execute(
        """INSERT OR IGNORE INTO review_state
           (puzzle_id, ease_factor, interval_days, repetitions, due_date,
            last_reviewed_at, consecutive_correct, consecutive_wrong)
           VALUES (?, 2.5, 0, 0, ?, NULL, 0, 0)""",
        (puzzle.id, dt.datetime.now().isoformat()),
    )


def replace_puzzles_for_games(conn: sqlite3.Connection, game_ids: List[str],
                              puzzles: List[Puzzle]) -> None:
    """Deletes the existing puzzles (and review state) for these games and
    inserts the new set, in one transaction — used both after analyzing a
    game and when regenerating everything at a new threshold."""
    with conn:
        for game_id in game_ids:
            conn.execute(
                """DELETE FROM review_state WHERE puzzle_id IN
                   (SELECT id FROM puzzles WHERE source_game_id = ?)""",
                (game_id,),
            )
            conn.execute("DELETE FROM puzzles WHERE source_game_id = ?", (game_id,))
        for p in puzzles:
            _insert_puzzle(conn, p)


def _row_to_puzzle(r: sqlite3.Row) -> Puzzle:
    return Puzzle(
        id=r["id"], fen=r["fen"], correct_move_san=r["correct_move_san"],
        correct_move_uci=r["correct_move_uci"], source=PuzzleSource(r["source"]),
        played_move_san=r["played_move_san"], eval_swing_cp=r["eval_swing_cp"],
        eval_before_cp=r["eval_before_cp"] if "eval_before_cp" in r.keys() else None,
        eval_after_cp=r["eval_after_cp"] if "eval_after_cp" in r.keys() else None,
        puzzle_type=r["puzzle_type"] if "puzzle_type" in r.keys() else None,
        source_game_id=r["source_game_id"], opening_line_id=r["opening_line_id"],
        created_at=dt.datetime.fromisoformat(r["created_at"]),
    )


def due_puzzles(conn: sqlite3.Connection, limit: int = 20) -> List[Puzzle]:
    # ORDER BY RANDOM() samples randomly from everything currently due,
    # rather than always surfacing the most-overdue ones first
    rows = conn.execute(
        """SELECT p.* FROM puzzles p
           JOIN review_state rs ON rs.puzzle_id = p.id
           WHERE rs.due_date <= ?
           ORDER BY RANDOM()
           LIMIT ?""",
        (dt.datetime.now().isoformat(), limit),
    ).fetchall()
    return [_row_to_puzzle(r) for r in rows]


def all_puzzles(conn: sqlite3.Connection, puzzle_type: Optional[str] = None,
                time_classes: Optional[set] = None) -> List[Puzzle]:
    """All puzzles, optionally filtered by type and by the source game's
    time class, in random order — this is the browsing queue (no
    spaced-repetition due-date gating)."""
    rows = conn.execute(
        """SELECT p.*, g.time_control AS game_time_control FROM puzzles p
           LEFT JOIN games g ON g.id = p.source_game_id
           WHERE (? IS NULL OR p.puzzle_type = ?)
           ORDER BY RANDOM()""",
        (puzzle_type, puzzle_type),
    ).fetchall()
    return [
        _row_to_puzzle(r) for r in rows
        if in_time_filter(r["game_time_control"], time_classes)
    ]


def distinct_puzzle_types(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT DISTINCT puzzle_type FROM puzzles WHERE puzzle_type IS NOT NULL ORDER BY puzzle_type"
    ).fetchall()
    return [r["puzzle_type"] for r in rows]


# ---------- Review state ----------

def load_review_state(conn: sqlite3.Connection, puzzle_id: str) -> ReviewState:
    r = conn.execute(
        "SELECT * FROM review_state WHERE puzzle_id = ?", (puzzle_id,)
    ).fetchone()
    return ReviewState(
        puzzle_id=r["puzzle_id"], ease_factor=r["ease_factor"],
        interval_days=r["interval_days"], repetitions=r["repetitions"],
        due_date=dt.datetime.fromisoformat(r["due_date"]),
        last_reviewed_at=dt.datetime.fromisoformat(r["last_reviewed_at"]) if r["last_reviewed_at"] else None,
        consecutive_correct=r["consecutive_correct"],
        consecutive_wrong=r["consecutive_wrong"],
    )


def save_review_state(conn: sqlite3.Connection, rs: ReviewState) -> None:
    conn.execute(
        """UPDATE review_state SET ease_factor=?, interval_days=?, repetitions=?,
           due_date=?, last_reviewed_at=?, consecutive_correct=?, consecutive_wrong=?
           WHERE puzzle_id=?""",
        (
            rs.ease_factor, rs.interval_days, rs.repetitions,
            rs.due_date.isoformat(),
            rs.last_reviewed_at.isoformat() if rs.last_reviewed_at else None,
            rs.consecutive_correct, rs.consecutive_wrong, rs.puzzle_id,
        ),
    )
    conn.commit()


# ---------- Opening lines ----------

def save_opening_line(conn: sqlite3.Connection, line: OpeningLine) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO opening_lines
           (id, move_san, move_uci, fen_after_move, repertoire_name,
            my_color, move_number, parent_id, is_line_end, line_name,
            is_enabled, fen_before_move, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            line.id, line.move_san, line.move_uci, line.fen_after_move,
            line.repertoire_name, line.my_color.value, line.move_number,
            line.parent_id, int(line.is_line_end), line.line_name,
            int(line.is_enabled), line.fen_before_move, line.notes,
        ),
    )
    conn.commit()


def _row_to_opening_line(r: sqlite3.Row) -> OpeningLine:
    return OpeningLine(
        id=r["id"], move_san=r["move_san"], move_uci=r["move_uci"],
        fen_after_move=r["fen_after_move"], repertoire_name=r["repertoire_name"],
        my_color=PlayerColor(r["my_color"]), move_number=r["move_number"],
        parent_id=r["parent_id"], is_line_end=bool(r["is_line_end"]),
        line_name=r["line_name"] if "line_name" in r.keys() else None,
        is_enabled=bool(r["is_enabled"]) if "is_enabled" in r.keys() and r["is_enabled"] is not None else True,
        fen_before_move=r["fen_before_move"] if "fen_before_move" in r.keys() else None,
        notes=r["notes"],
    )


def find_line_node(conn: sqlite3.Connection, repertoire_name: str, my_color: str,
                   line_name: str, parent_id: Optional[str], move_uci: str) -> Optional[OpeningLine]:
    """The existing node for this move in this line, if the line was
    imported before — lets a re-import update notes instead of duplicating."""
    row = conn.execute(
        """SELECT * FROM opening_lines
           WHERE repertoire_name = ? AND my_color = ? AND line_name IS ?
           AND parent_id IS ? AND move_uci = ?""",
        (repertoire_name, my_color, line_name, parent_id, move_uci),
    ).fetchone()
    return _row_to_opening_line(row) if row else None


def set_line_notes(conn: sqlite3.Connection, node_id: str, notes: Optional[str]) -> None:
    conn.execute("UPDATE opening_lines SET notes = ? WHERE id = ?", (notes, node_id))
    conn.commit()


def list_repertoires(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Returns distinct (repertoire_name, my_color) pairs available to drill."""
    rows = conn.execute(
        "SELECT DISTINCT repertoire_name, my_color FROM opening_lines"
    ).fetchall()
    return [(r["repertoire_name"], r["my_color"]) for r in rows]


def lines_at_fen(conn: sqlite3.Connection, repertoire_name: str, my_color: str, fen: str) -> List[OpeningLine]:
    """Every enabled line in this repertoire playable from a given board
    position — matched by the actual position, not by database parent-child
    links. This is what makes book-move detection work across lines that
    were imported as SEPARATE PGN games but happen to share a position
    (e.g. several Queen's Gambit lines all starting 1.d4): they're
    unrelated rows in the database, but this matches them by FEN instead
    of by shared ancestry, so they're correctly treated as alternatives at
    that position."""
    rows = conn.execute(
        """SELECT * FROM opening_lines
           WHERE repertoire_name = ? AND my_color = ? AND fen_before_move = ?
           AND is_enabled = 1""",
        (repertoire_name, my_color, fen),
    ).fetchall()
    return [_row_to_opening_line(r) for r in rows]


def root_lines(conn: sqlite3.Connection, repertoire_name: str, my_color: str) -> List[OpeningLine]:
    """Ply-1 nodes (parent_id IS NULL) for a given repertoire — enabled
    ones only, since this is what drilling draws from."""
    rows = conn.execute(
        """SELECT * FROM opening_lines
           WHERE repertoire_name = ? AND my_color = ? AND parent_id IS NULL
           AND is_enabled = 1""",
        (repertoire_name, my_color),
    ).fetchall()
    return [_row_to_opening_line(r) for r in rows]


def children_of(conn: sqlite3.Connection, parent_id: str) -> List[OpeningLine]:
    """Enabled children only — used during drilling/book-move checks."""
    rows = conn.execute(
        "SELECT * FROM opening_lines WHERE parent_id = ? AND is_enabled = 1", (parent_id,)
    ).fetchall()
    return [_row_to_opening_line(r) for r in rows]


def all_root_lines(conn: sqlite3.Connection, repertoire_name: str, my_color: str) -> List[OpeningLine]:
    """Every root line regardless of enabled state — used by the 'manage
    lines' menu, which needs to show and toggle disabled lines too."""
    rows = conn.execute(
        """SELECT * FROM opening_lines
           WHERE repertoire_name = ? AND my_color = ? AND parent_id IS NULL
           ORDER BY line_name""",
        (repertoire_name, my_color),
    ).fetchall()
    return [_row_to_opening_line(r) for r in rows]


def _all_children_of(conn: sqlite3.Connection, parent_id: str) -> List[OpeningLine]:
    """Unfiltered children (includes disabled) — internal helper for
    cascading enable/disable down a whole line regardless of current state."""
    rows = conn.execute(
        "SELECT * FROM opening_lines WHERE parent_id = ?", (parent_id,)
    ).fetchall()
    return [_row_to_opening_line(r) for r in rows]


def set_line_enabled(conn: sqlite3.Connection, root_id: str, enabled: bool) -> None:
    """Toggles a whole line (the root plus every descendant) on or off,
    without deleting anything."""
    stack = [root_id]
    ids_to_update = []
    while stack:
        node_id = stack.pop()
        ids_to_update.append(node_id)
        stack.extend(child.id for child in _all_children_of(conn, node_id))
    conn.executemany(
        "UPDATE opening_lines SET is_enabled = ? WHERE id = ?",
        [(int(enabled), nid) for nid in ids_to_update],
    )
    conn.commit()
