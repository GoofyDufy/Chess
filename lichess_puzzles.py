"""Puzzles from the Lichess puzzle database (database.lichess.org, CC0),
kept apart from the puzzles made from your own games:

  - their own table (lichess_puzzles), so Stats / Progress / Weak spots of
    your games never mix with them;
  - tries are saved as attempts with item_type 'lichess' (they still earn XP);
  - Puzzle Review's "Puzzles" dropdown picks My games / Lichess / Both.

The file (~300 MB compressed, millions of puzzles) is never saved: it's
streamed and decompressed on the fly, and we stop as soon as we have enough
puzzles in the chosen rating range. It's sorted by puzzle id, which is
effectively random, so the first N that fit are a fair sample; already
imported ids are skipped, so the next import reaches further into the file.

Lichess's format: FEN is the position BEFORE the opponent's move; Moves
starts with that move, then the solution (yours and the replies). We store
the position after it (your move) and keep the opponent's move to show it.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
from typing import Callable, Dict, Iterator, List, Optional

import chess

from models import Puzzle, PuzzleSource

URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
ITEM_TYPE = "lichess"            # attempts.item_type for these puzzles
# quality: well-played puzzles with a settled rating
MIN_POPULARITY = 80              # -100..100, players' thumbs up/down
MIN_PLAYS = 300
MAX_RATING_DEVIATION = 100
DEFAULT_COUNT = 5000
DEFAULT_WINDOW = 300             # rating range around yours by default
CHESS_COM_TO_LICHESS = 200       # same rough offset as the opening explorer

# Lichess theme -> our puzzle type (first match wins), shown in the Type filter
THEME_TYPES = (
    ("mateIn1", "Mate in 1"), ("mateIn2", "Mate in 2"), ("mateIn3", "Mate in 3"),
    ("mateIn4", "Mate in 4+"), ("mateIn5", "Mate in 4+"), ("mate", "Mate in 4+"),
    ("fork", "Fork"), ("pin", "Pin"), ("skewer", "Skewer"),
    ("discoveredAttack", "Discovered Attack"), ("doubleCheck", "Discovered Attack"),
    ("hangingPiece", "Hanging Piece"), ("trappedPiece", "Trapped Piece"),
    ("sacrifice", "Sacrifice"), ("deflection", "Deflection"), ("attraction", "Deflection"),
    ("interference", "Deflection"), ("clearance", "Deflection"),
    ("xRayAttack", "X-Ray"), ("zugzwang", "Zugzwang"), ("promotion", "Promotion"),
    ("advancedPawn", "Promotion"), ("defensiveMove", "Defensive Move"), ("quietMove", "Quiet Move"),
    ("capturingDefender", "Deflection"), ("intermezzo", "Intermezzo"),
    ("kingsideAttack", "King Attack"), ("queensideAttack", "King Attack"), ("attackingF2F7", "King Attack"),
    ("exposedKing", "King Attack"),
    # no named motif: by phase / what it wins
    ("rookEndgame", "Endgame"), ("pawnEndgame", "Endgame"), ("bishopEndgame", "Endgame"),
    ("knightEndgame", "Endgame"), ("queenEndgame", "Endgame"), ("queenRookEndgame", "Endgame"),
    ("endgame", "Endgame"), ("crushing", "Win Material"), ("advantage", "Win Material"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lichess_puzzles (
    id TEXT PRIMARY KEY,          -- Lichess puzzle id
    setup_fen TEXT,               -- before the opponent's move
    setup_uci TEXT,               -- the opponent's move that sets the puzzle up
    fen TEXT,                     -- after it: your move
    solution_ucis TEXT,           -- your moves and the replies
    rating INTEGER,
    popularity INTEGER,
    plays INTEGER,
    themes TEXT,
    game_url TEXT,
    opening TEXT,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lichess_puzzles_rating ON lichess_puzzles (rating);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def puzzle_type(themes: List[str]) -> str:
    have = set(themes)
    for theme, name in THEME_TYPES:
        if theme in have:
            return name
    return "Tactic"


def kind(themes: List[str]) -> str:
    """Attacking / Defensive / Positional, like puzzle_kind.py's kinds."""
    have = set(themes)
    if "defensiveMove" in have:
        return "Defensive"
    if "quietMove" in have and not have & {"mate", "fork", "pin", "skewer", "hangingPiece"}:
        return "Positional"
    return "Attacking"


def parse(row: List[str]) -> Optional[Dict]:
    """A CSV row -> a lichess_puzzles row, or None if it's malformed."""
    try:
        pid, fen, moves, rating, deviation, popularity, plays, themes, url = row[:9]
        opening = row[9] if len(row) > 9 else ""
        ucis = moves.split()
        board = chess.Board(fen)
        setup = chess.Move.from_uci(ucis[0])
        if setup not in board.legal_moves or len(ucis) < 2:
            return None
        board.push(setup)
        return {
            "id": pid, "setup_fen": fen, "setup_uci": ucis[0], "fen": board.fen(),
            "solution_ucis": " ".join(ucis[1:]), "rating": int(rating), "deviation": int(deviation),
            "popularity": int(popularity), "plays": int(plays), "themes": themes,
            "game_url": url, "opening": opening.split(" ")[0].replace("_", " ") if opening else "",
        }
    except (ValueError, IndexError):
        return None


def good(p: Dict, min_rating: int, max_rating: int) -> bool:
    return (min_rating <= p["rating"] <= max_rating and p["popularity"] >= MIN_POPULARITY
            and p["plays"] >= MIN_PLAYS and p["deviation"] <= MAX_RATING_DEVIATION)


def rows(stream: io.TextIOBase) -> Iterator[List[str]]:
    reader = csv.reader(stream)
    next(reader, None)                         # header
    yield from reader


def download(min_rating: int, max_rating: int, count: int, skip: set,
             on_batch: Callable[[List[Dict]], None], stop: threading.Event,
             on_progress: Optional[Callable[[int, int], None]] = None, batch: int = 500,
             open_stream: Optional[Callable[[], io.TextIOBase]] = None) -> int:
    """Streams the Lichess file and hands batches of new, good puzzles to
    on_batch (from this thread — the caller saves them on its own thread).
    on_progress(found, scanned). Returns how many were found."""
    found, scanned, pending = 0, 0, []
    stream, close = (open_stream(), None) if open_stream else _open_remote()
    try:
        for row in rows(stream):
            if stop.is_set() or found >= count:
                break
            scanned += 1
            if row and row[0] in skip:
                continue
            p = parse(row) if row else None
            if p is None or not good(p, min_rating, max_rating):
                if on_progress and scanned % 20000 == 0:
                    on_progress(found, scanned)
                continue
            pending.append(p)
            found += 1
            if len(pending) >= batch:
                on_batch(pending)
                pending = []
                if on_progress:
                    on_progress(found, scanned)
        if pending:
            on_batch(pending)
        if on_progress:
            on_progress(found, scanned)
    finally:
        if close:
            close()
    return found


def _open_remote():
    import requests
    import zstandard
    resp = requests.get(URL, stream=True, timeout=30)
    resp.raise_for_status()
    text = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(resp.raw), encoding="utf-8")
    return text, resp.close


# ---------- the database ----------

def save(conn: sqlite3.Connection, puzzles: List[Dict]) -> None:
    with conn:
        conn.executemany(
            """INSERT OR IGNORE INTO lichess_puzzles
               (id, setup_fen, setup_uci, fen, solution_ucis, rating, popularity, plays, themes, game_url, opening)
               VALUES (:id, :setup_fen, :setup_uci, :fen, :solution_ucis, :rating, :popularity, :plays,
                       :themes, :game_url, :opening)""", puzzles)


def ids(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT id FROM lichess_puzzles")}


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM lichess_puzzles").fetchone()[0]


def rating_span(conn: sqlite3.Connection):
    return conn.execute("SELECT MIN(rating), MAX(rating) FROM lichess_puzzles").fetchone()


def remove_all(conn: sqlite3.Connection) -> None:
    """Removes the puzzles; your tries stay in attempts (XP isn't lost)."""
    with conn:
        conn.execute("DELETE FROM lichess_puzzles")


def default_range(my_chess_com_rating: Optional[int]) -> tuple:
    centre = (my_chess_com_rating or 1300) + CHESS_COM_TO_LICHESS
    return max(400, centre - DEFAULT_WINDOW), centre + DEFAULT_WINDOW


def load(conn: sqlite3.Connection, puzzle_type_filter: Optional[str] = None) -> List[Puzzle]:
    out = []
    for r in conn.execute("SELECT * FROM lichess_puzzles ORDER BY RANDOM()"):
        p = to_puzzle(r)
        if puzzle_type_filter is None or p.puzzle_type == puzzle_type_filter:
            out.append(p)
    return out


def to_puzzle(r) -> Puzzle:
    themes = (r["themes"] or "").split()
    ucis = r["solution_ucis"].split()
    board = chess.Board(r["fen"])
    first = chess.Move.from_uci(ucis[0])
    return Puzzle(
        id=r["id"], fen=r["fen"], correct_move_san=board.san(first), correct_move_uci=ucis[0],
        source=PuzzleSource.LICHESS, puzzle_type=puzzle_type(themes),
        solution_ucis=r["solution_ucis"], solution_moves=(len(ucis) + 1) // 2, kind=kind(themes),
        rating=r["rating"], themes=r["themes"], game_url=r["game_url"], opening=r["opening"],
        setup_fen=r["setup_fen"], setup_uci=r["setup_uci"],
    )


def nice_themes(themes: Optional[str]) -> str:
    """'crushing hangingPiece long' -> 'crushing, hanging piece, long'."""
    words = []
    for t in (themes or "").split():
        words.append("".join(" " + c.lower() if c.isupper() else c for c in t))
    return ", ".join(words)
