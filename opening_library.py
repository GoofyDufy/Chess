"""The opening library: every named opening and variation from Lichess's open
dataset (github.com/lichess-org/chess-openings, CC0) — about 3,900 lines
with their ECO code, name and moves. Downloaded once (≈400 KB) into the
opening_library table; from Opening Drill → Add lines → Opening library you
search or browse it and add lines to a repertoire to drill.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import chess

URL = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{part}.tsv"
PARTS = "abcde"
SOURCE = "Lichess opening names (github.com/lichess-org/chess-openings, CC0)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS opening_library (
    id INTEGER PRIMARY KEY,
    eco TEXT,
    name TEXT,                -- "Sicilian Defense: Najdorf Variation"
    family TEXT,              -- "Sicilian Defense"
    pgn TEXT,                 -- "1. e4 c5 2. Nf3 d6 ..."
    sans TEXT,                -- "e4 c5 Nf3 d6 ..." (for searching by moves)
    ucis TEXT,
    plies INTEGER
);
CREATE INDEX IF NOT EXISTS idx_opening_library_family ON opening_library (family);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # sharp / quiet, described by library_character.py (in the background, once)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(opening_library)")}
    for col, kind in (("character", "TEXT"), ("sharpness", "INTEGER"), ("description", "TEXT"),
                      ("eval_cp", "INTEGER"), ("char_version", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE opening_library ADD COLUMN {col} {kind}")


@dataclass
class Opening:
    id: int
    eco: str
    name: str
    family: str
    pgn: str
    sans: List[str]
    ucis: List[str]
    character: Optional[str] = None       # "Sharp" / "Balanced" / "Quiet" (None: not described yet)
    description: Optional[str] = None
    eval_cp: Optional[int] = None

    @property
    def variation(self) -> str:
        return self.name.split(":", 1)[1].strip() if ":" in self.name else ""

    @property
    def plies(self) -> int:
        return len(self.ucis)

    @property
    def last_move(self) -> str:
        """'6. Be3' / '8...h5': tells apart lines sharing a name (different move orders)."""
        if not self.sans:
            return ""
        n = len(self.sans) - 1
        return f"{n // 2 + 1}{'.' if n % 2 == 0 else '...'} {self.sans[-1]}"


# ---------- download (background thread: no database here) ----------

def parse(tsv: str) -> List[Dict]:
    out = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        board, sans, ucis = chess.Board(), [], []
        try:
            for token in row["pgn"].split():
                if token.endswith("."):
                    continue
                move = board.parse_san(token)
                sans.append(board.san(move))
                ucis.append(move.uci())
                board.push(move)
        except (ValueError, KeyError):
            continue
        name = row["name"].strip()
        out.append({"eco": row["eco"].strip(), "name": name, "family": name.split(":", 1)[0].strip(),
                    "pgn": row["pgn"].strip(), "sans": " ".join(sans), "ucis": " ".join(ucis),
                    "plies": len(ucis)})
    return out


def download() -> List[Dict]:
    import requests
    rows = []
    for part in PARTS:
        resp = requests.get(URL.format(part=part), timeout=30)
        resp.raise_for_status()
        rows += parse(resp.text)
    return rows


# ---------- the database ----------

def save(conn: sqlite3.Connection, rows: List[Dict]) -> None:
    with conn:
        conn.execute("DELETE FROM opening_library")
        conn.executemany("INSERT INTO opening_library (eco, name, family, pgn, sans, ucis, plies) "
                         "VALUES (:eco, :name, :family, :pgn, :sans, :ucis, :plies)", rows)


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM opening_library").fetchone()[0]


def _row(r) -> Opening:
    keys = r.keys()
    return Opening(r["id"], r["eco"], r["name"], r["family"], r["pgn"], r["sans"].split(), r["ucis"].split(),
                   r["character"] if "character" in keys else None,
                   r["description"] if "description" in keys else None,
                   r["eval_cp"] if "eval_cp" in keys else None)


def families(conn: sqlite3.Connection) -> List[Tuple[str, int]]:
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT family, COUNT(*) FROM opening_library GROUP BY family ORDER BY family")]


def search(conn: sqlite3.Connection, query: str = "", family: Optional[str] = None, limit: int = 600,
           character: Optional[str] = None) -> List[Opening]:
    """By name, ECO code (B90, or B9 for the B90s) or moves ('e4 c5 Nf3' — the line starts so);
    character: only Sharp / Balanced / Quiet lines."""
    q = " ".join(query.split())
    sql, args = "SELECT * FROM opening_library WHERE 1 = 1", []
    if family:
        sql += " AND family = ?"
        args.append(family)
    if character:
        sql += " AND character = ?"
        args.append(character)
    if q:
        sql += " AND (name LIKE ? OR eco LIKE ? OR sans LIKE ? OR sans = ?)"
        args += [f"%{q}%", f"{q}%", f"{q} %", q]
    sql += " ORDER BY eco, plies, name LIMIT ?"
    args.append(limit)
    return [_row(r) for r in conn.execute(sql, args)]


def get(conn: sqlite3.Connection, ids: List[int]) -> List[Opening]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [_row(r) for r in conn.execute(f"SELECT * FROM opening_library WHERE id IN ({marks})", ids)]


# ---------- your repertoire ----------

def repertoire_moves(conn: sqlite3.Connection, color: str) -> Set[Tuple[str, str]]:
    """(position, move) pairs of every enabled line of that colour."""
    return {(" ".join(r[0].split()[:4]), r[1]) for r in conn.execute(
        "SELECT fen_before_move, move_uci FROM opening_lines WHERE my_color = ? AND is_enabled = 1", (color,))}


def covered_plies(opening: Opening, moves: Set[Tuple[str, str]]) -> int:
    """How many of the line's first moves your repertoire (that colour) already has."""
    board, n = chess.Board(), 0
    for uci in opening.ucis:
        if (" ".join(board.fen().split()[:4]), uci) not in moves:
            break
        board.push_uci(uci)
        n += 1
    return n


def covered(opening: Opening, moves: Set[Tuple[str, str]]) -> bool:
    """Is every move of this line already in your repertoire (that colour)?"""
    return covered_plies(opening, moves) == opening.plies


PARTLY_MIN = 4             # "partly in your repertoire": at least this many of its moves, and half of it


def status(opening: Opening, mine: Dict[str, Set[Tuple[str, str]]]) -> Tuple[str, Optional[str], int]:
    """("in" / "partly" / "out", the colour, moves covered) — the best of White and Black."""
    best = max(("white", "black"), key=lambda c: covered_plies(opening, mine.get(c, set())))
    n = covered_plies(opening, mine.get(best, set()))
    if n == opening.plies:
        return "in", best, n
    if n >= PARTLY_MIN and n * 2 >= opening.plies:
        return "partly", best, n
    return "out", None, n


# ---------- sharp / quiet (library_character.py) ----------

def undescribed(conn: sqlite3.Connection, version: int) -> List[Opening]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM opening_library WHERE char_version IS NULL OR char_version != ? ORDER BY eco, plies", (version,))]


def save_character(conn: sqlite3.Connection, items, version: int) -> None:
    """items: [(opening id, library_character.Character)]."""
    with conn:
        conn.executemany(
            "UPDATE opening_library SET character = ?, sharpness = ?, description = ?, eval_cp = ?, char_version = ? "
            "WHERE id = ?", [(c.label, c.score, c.text, c.eval_cp, version, oid) for oid, c in items])


def to_pgn(openings: List[Opening]) -> str:
    """One game per line, its name as the [Event] (the line's name once imported)."""
    games = []
    for o in openings:
        name = o.name.replace('"', "'")
        games.append(f'[Event "{name}"]\n[ECO "{o.eco}"]\n\n{o.pgn} *\n')
    return "\n".join(games)
