"""Lichess Opening Explorer: how often players near your rating play each
move in a position, and how they score with it.

The explorer needs a (free) Lichess personal access token with no
permissions — it's kept in this player's database (settings
'lichess_token'), which git ignores. Answers are cached in the
explorer_cache table, so each position is downloaded once and drills keep
working offline.

Network calls never happen on the UI thread: fetch_stats() is called from
a background thread (warm()), and the UI only reads the cache (cached()).
Lichess asks API clients to send one request at a time and to wait a
minute after a 429 (rate limited) — warm() does both.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

import chess
import requests

import db
from repertoire_gaps import position_key

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"
ACCOUNT_URL = "https://lichess.org/api/account"
TOKEN_PAGE = "https://lichess.org/account/oauth/token"
HEADERS = {"User-Agent": "kingcoach-desktop/0.1 (personal use)"}
TOKEN_SETTING = "lichess_token"

SPEEDS = "blitz,rapid"
RATING_BUCKETS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)
# chess.com ratings run roughly this much below Lichess for the same strength
# (rough rule of thumb — only used to pick which rating bands to ask for)
CHESS_COM_TO_LICHESS = 200
RATING_WINDOW = 200            # ask for bands within ± this of your estimated Lichess rating
CACHE_DAYS = 60
RATE_LIMIT_WAIT_S = 61
REQUEST_GAP_S = 0.3            # pause between requests while warming the cache

_ELO_RE = {"white": re.compile(r'\[WhiteElo "(\d+)"\]'), "black": re.compile(r'\[BlackElo "(\d+)"\]')}


class ExplorerError(Exception):
    pass


class BadToken(ExplorerError):
    pass


@dataclass
class MoveStat:
    uci: str
    san: str
    games: int
    white: int
    draws: int
    black: int

    def score_for(self, color: chess.Color) -> float:
        """Points per game (win 1, draw ½) for `color` after this move."""
        if not self.games:
            return 0.5
        wins = self.white if color == chess.WHITE else self.black
        return (wins + 0.5 * self.draws) / self.games


@dataclass
class PositionStats:
    games: int
    moves: List[MoveStat]

    def share(self, uci: str) -> float:
        """Fraction of games in this position where `uci` was played."""
        if not self.games:
            return 0.0
        return next((m.games for m in self.moves if m.uci == uci), 0) / self.games


# ---------- token ----------

def get_token(conn: sqlite3.Connection) -> Optional[str]:
    return db.get_setting(conn, TOKEN_SETTING) or None


def set_token(conn: sqlite3.Connection, token: Optional[str]) -> None:
    if token:
        db.set_setting(conn, TOKEN_SETTING, token)
    else:
        conn.execute("DELETE FROM settings WHERE key = ?", (TOKEN_SETTING,))
        conn.commit()


def check_token(token: str) -> str:
    """The Lichess username the token belongs to; raises BadToken / ExplorerError."""
    try:
        resp = requests.get(ACCOUNT_URL, headers=_auth(token), timeout=10)
    except requests.RequestException as e:
        raise ExplorerError(f"Couldn't reach Lichess: {e}") from e
    if resp.status_code == 401:
        raise BadToken("Lichess didn't accept that token.")
    if resp.status_code != 200:
        raise ExplorerError(f"Lichess answered with an error ({resp.status_code}).")
    return resp.json().get("username", "?")


def _auth(token: str) -> Dict[str, str]:
    return {**HEADERS, "Authorization": f"Bearer {token}"}


# ---------- which players to compare with ----------

def my_recent_rating(conn: sqlite3.Connection, games: int = 50) -> Optional[int]:
    """Median of your chess.com rating over your most recent games."""
    ratings = []
    for r in conn.execute("SELECT pgn, my_color FROM games ORDER BY played_at DESC LIMIT ?", (games,)):
        m = _ELO_RE[r["my_color"]].search(r["pgn"])
        if m:
            ratings.append(int(m.group(1)))
    return int(statistics.median(ratings)) if ratings else None


def rating_bands(conn: sqlite3.Connection) -> str:
    """Lichess rating bands around your level, e.g. '1600,1800'."""
    rating = my_recent_rating(conn)
    if rating is None:
        return "1400,1600,1800"
    est = rating + CHESS_COM_TO_LICHESS
    bands = []
    for i, low in enumerate(RATING_BUCKETS):
        high = RATING_BUCKETS[i + 1] if i + 1 < len(RATING_BUCKETS) else 10_000
        if low < est + RATING_WINDOW and high > est - RATING_WINDOW:
            bands.append(str(low))
    return ",".join(bands)


def describe_bands(bands: str) -> str:
    """'1600,1800' -> 'rated 1600–2000' (Lichess ratings)."""
    lows = sorted(int(b) for b in bands.split(","))
    nxt = [b for b in RATING_BUCKETS if b > lows[-1]]
    return f"rated {lows[0]}–{nxt[0]}" if nxt else f"rated {lows[0]}+"


# ---------- cache ----------

def _cache_key(bands: str, fen: str) -> str:
    return f"{bands}|{SPEEDS}|{position_key(fen)}"


def cached(conn: sqlite3.Connection, fen: str, bands: Optional[str] = None) -> Optional[PositionStats]:
    """Cached stats for this position (no network), or None."""
    bands = bands or rating_bands(conn)
    row = conn.execute("SELECT data FROM explorer_cache WHERE key = ?", (_cache_key(bands, fen),)).fetchone()
    return _parse(json.loads(row["data"]), fen) if row else None


def save(conn: sqlite3.Connection, fen: str, bands: str, data: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO explorer_cache (key, data, fetched_at) VALUES (?, ?, ?)",
        (_cache_key(bands, fen), json.dumps(data), dt.datetime.now().isoformat()),
    )
    conn.commit()


def missing(conn: sqlite3.Connection, fens: Iterable[str], bands: str) -> List[str]:
    """Positions with no fresh cache entry (one per distinct position)."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=CACHE_DAYS)).isoformat()
    out, seen = [], set()
    for fen in fens:
        key = _cache_key(bands, fen)
        if key in seen:
            continue
        seen.add(key)
        row = conn.execute("SELECT fetched_at FROM explorer_cache WHERE key = ?", (key,)).fetchone()
        if row is None or row["fetched_at"] < cutoff:
            out.append(fen)
    return out


def _parse(data: dict, fen: str) -> PositionStats:
    moves = []
    for m in data.get("moves", []):
        games = m.get("white", 0) + m.get("draws", 0) + m.get("black", 0)
        moves.append(MoveStat(m["uci"], m.get("san", m["uci"]), games,
                              m.get("white", 0), m.get("draws", 0), m.get("black", 0)))
    total = data.get("white", 0) + data.get("draws", 0) + data.get("black", 0)
    # castling comes back as king-takes-rook (e1h1) for some positions; use the
    # standard form (e1g1) so it matches repertoire moves
    board = chess.Board(fen)
    for m in moves:
        try:
            m.uci = board.parse_uci(m.uci).uci()
        except ValueError:
            pass
    return PositionStats(total, moves)


# ---------- network (background threads only) ----------

def fetch_stats(token: str, fen: str, bands: str) -> dict:
    """Raw explorer answer for one position. Raises BadToken, or
    ExplorerError('rate limited') on 429."""
    params = {"variant": "standard", "fen": fen, "speeds": SPEEDS, "ratings": bands,
              "moves": 12, "topGames": 0, "recentGames": 0}
    try:
        resp = requests.get(EXPLORER_URL, params=params, headers=_auth(token), timeout=15)
    except requests.RequestException as e:
        raise ExplorerError(f"Couldn't reach Lichess: {e}") from e
    if resp.status_code == 401:
        raise BadToken("Lichess didn't accept the saved token.")
    if resp.status_code == 429:
        raise ExplorerError("rate limited")
    if resp.status_code != 200:
        raise ExplorerError(f"Lichess answered with an error ({resp.status_code}).")
    return resp.json()


def warm(token: str, fens: List[str], bands: str,
         on_result: Callable[[str, dict], None],
         on_done: Callable[[int, Optional[str]], None],
         stop: Optional[threading.Event] = None) -> threading.Thread:
    """Downloads stats for `fens` one at a time on a background thread.
    on_result(fen, data) per position and on_done(fetched, error_or_None)
    are called FROM THAT THREAD — the caller must hand them to the UI
    thread (and do the cache writes there)."""
    def run():
        fetched, error = 0, None
        for fen in fens:
            if stop is not None and stop.is_set():
                break
            try:
                data = fetch_stats(token, fen, bands)
            except ExplorerError as e:
                if str(e) != "rate limited":
                    error = str(e)
                    break
                time.sleep(RATE_LIMIT_WAIT_S)
                try:
                    data = fetch_stats(token, fen, bands)
                except ExplorerError as e2:
                    error = str(e2)
                    break
            on_result(fen, data)
            fetched += 1
            time.sleep(REQUEST_GAP_S)
        on_done(fetched, error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


# ---------- what to download for a repertoire ----------

def opponent_positions(conn: sqlite3.Connection, repertoire_name: Optional[str] = None) -> List[str]:
    """Every position in your (enabled) repertoire where the OPPONENT moves
    next and your prep has an answer for them — the ones whose real-world
    replies matter for drills and gaps. All repertoires when
    repertoire_name is None."""
    sql = "SELECT fen_before_move, my_color FROM opening_lines WHERE is_enabled = 1"
    args: tuple = ()
    if repertoire_name is not None:
        sql += " AND repertoire_name = ?"
        args = (repertoire_name,)
    fens = []
    for r in conn.execute(sql, args):
        mine = chess.WHITE if r["my_color"] == "white" else chess.BLACK
        fen = r["fen_before_move"]
        if fen and chess.Board(fen).turn != mine:
            fens.append(fen)
    return list(dict.fromkeys(fens))
