"""Your openings as you actually play them (Opening Drill → My games): the
openings you play most, and every place where YOU leave your own
repertoire — the move your prep has there, what you played instead, and how
each scores for you. Pure functions, no engine (opening_commentary.py
explains a deviation with Stockfish, on demand).

Each game's first MAX_PLY half-moves are replayed against all your enabled
lines for the colour you had, matched by position (transpositions count):
  - your move at a position your prep covers, and it's a prep move: followed
  - your move there, and it isn't: a deviation (only the first in a game)
  - the opponent's move at a position your prep covers isn't in your prep:
    the opponent left it (an OpponentDeviation — with your reply and how
    you did); the game is out of book from then on
  - your prep ends: out of book too, nothing more is counted.
"""

from __future__ import annotations

import io
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import chess
import chess.pgn

from models import in_time_filter
from repertoire_gaps import _opening_moves, format_line, position_key
from stats import Record, opening_name

MAX_PLY = 24             # the first 12 moves
MIN_GAMES = 1


@dataclass
class OpeningUse:
    name: str
    color: str
    record: Record = field(default_factory=Record)
    followed: int = 0          # games where you never left your prep
    left: int = 0              # games where you left it yourself
    no_prep: int = 0           # games your repertoire had nothing for
    deviation_keys: Counter = field(default_factory=Counter)

    @property
    def games(self) -> int:
        return self.record.games


@dataclass
class MyDeviation:
    color: str
    prefix: List[str]          # SAN moves up to the position
    fen: str                   # the position (your move)
    played_uci: str
    played_san: str
    prep_ucis: List[str]       # what your repertoire plays here
    prep_sans: List[str]
    record: Record = field(default_factory=Record)       # games you played the deviation
    prep_record: Record = field(default_factory=Record)  # games you played your prep move here
    opening: str = ""
    repertoires: List[str] = field(default_factory=list)
    game_ids: List[str] = field(default_factory=list)

    @property
    def games(self) -> int:
        return self.record.games

    @property
    def line(self) -> str:
        return format_line(self.prefix + [self.played_san])

    @property
    def move_number(self) -> int:
        return len(self.prefix) // 2 + 1

    @property
    def key(self) -> str:
        return f"{position_key(self.fen)}|{self.played_uci}"


@dataclass
class OpponentDeviation:
    color: str                 # your colour in these games
    prefix: List[str]
    fen: str                   # the position (their move)
    played_uci: str            # their move, outside your prep
    played_san: str
    prep_ucis: List[str]       # what your prep expects here
    prep_sans: List[str]
    record: Record = field(default_factory=Record)
    replies: Counter = field(default_factory=Counter)      # your reply (SAN) -> games
    opening: str = ""
    game_ids: List[str] = field(default_factory=list)

    @property
    def games(self) -> int:
        return self.record.games

    @property
    def line(self) -> str:
        return format_line(self.prefix + [self.played_san])

    @property
    def usual_reply(self) -> Optional[str]:
        return self.replies.most_common(1)[0][0] if self.replies else None


@dataclass
class Report:
    games: int = 0
    openings: List[OpeningUse] = field(default_factory=list)
    deviations: List[MyDeviation] = field(default_factory=list)
    opponent_deviations: List[OpponentDeviation] = field(default_factory=list)


def _repertoire(conn: sqlite3.Connection, color: str):
    moves: Dict[str, Set[str]] = defaultdict(set)
    names: Dict[str, Set[str]] = defaultdict(set)
    for r in conn.execute("SELECT fen_before_move, move_uci, repertoire_name FROM opening_lines "
                          "WHERE my_color = ? AND is_enabled = 1", (color,)):
        key = position_key(r["fen_before_move"])
        moves[key].add(r["move_uci"])
        names[key].add(r["repertoire_name"])
    return moves, names


def compute(conn: sqlite3.Connection, time_classes: Optional[Set[str]] = None) -> Report:
    report = Report()
    reps = {c: _repertoire(conn, c) for c in ("white", "black")}
    openings: Dict[Tuple[str, str], OpeningUse] = {}
    devs: Dict[str, MyDeviation] = {}
    dev_openings: Dict[str, Counter] = defaultdict(Counter)
    followed_at: Dict[str, Record] = defaultdict(Record)      # position -> games you played prep there
    opp: Dict[str, OpponentDeviation] = {}
    opp_openings: Dict[str, Counter] = defaultdict(Counter)

    for g in conn.execute("SELECT id, pgn, my_color, result, time_control FROM games"):
        if not in_time_filter(g["time_control"], time_classes):
            continue
        report.games += 1
        color = g["my_color"]
        mine = chess.WHITE if color == "white" else chess.BLACK
        rep_moves, rep_names = reps[color]
        headers = chess.pgn.read_headers(io.StringIO(g["pgn"])) or chess.pgn.Headers()
        name = opening_name(headers)
        use = openings.setdefault((name, color), OpeningUse(name, color))
        use.record.add(g["result"])

        board, sans = chess.Board(), []
        outcome, touched = "followed", False
        tokens = _opening_moves(g["pgn"], MAX_PLY + 1)
        for i, san in enumerate(tokens[:MAX_PLY]):
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            key = position_key(board.fen())
            prep = rep_moves.get(key)
            if not prep:
                break                                   # out of book (prep ended / opponent left)
            touched = True
            if board.turn == mine and move.uci() not in prep:
                dkey = f"{key}|{move.uci()}"
                if dkey not in devs:
                    devs[dkey] = MyDeviation(
                        color, list(sans), board.fen(), move.uci(), board.san(move), sorted(prep),
                        [board.san(chess.Move.from_uci(u)) for u in sorted(prep)],
                        repertoires=sorted(rep_names.get(key, ())))
                devs[dkey].record.add(g["result"])
                devs[dkey].game_ids.append(g["id"])
                dev_openings[dkey][name] += 1
                use.deviation_keys[dkey] += 1
                outcome = "left"
                break
            if board.turn == mine:
                followed_at[key].add(g["result"])
            elif move.uci() not in prep:                # the opponent left your prep
                okey = f"{key}|{move.uci()}"
                if okey not in opp:
                    opp[okey] = OpponentDeviation(
                        color, list(sans), board.fen(), move.uci(), board.san(move), sorted(prep),
                        [board.san(chess.Move.from_uci(u)) for u in sorted(prep)])
                o = opp[okey]
                o.record.add(g["result"])
                o.game_ids.append(g["id"])
                opp_openings[okey][name] += 1
                if i + 1 < len(tokens):                 # your reply
                    after = board.copy(stack=False)
                    after.push(move)
                    try:
                        o.replies[after.san(after.parse_san(tokens[i + 1]))] += 1
                    except ValueError:
                        pass
                break
            sans.append(board.san(move))
            board.push(move)
        if not touched:
            use.no_prep += 1
        elif outcome == "left":
            use.left += 1
        else:
            use.followed += 1

    for dkey, d in devs.items():
        d.opening = dev_openings[dkey].most_common(1)[0][0]
        d.prep_record = followed_at.get(position_key(d.fen), Record())
    for okey, o in opp.items():
        o.opening = opp_openings[okey].most_common(1)[0][0]
    report.opponent_deviations = sorted(opp.values(), key=lambda o: -o.games)
    report.openings = sorted(openings.values(), key=lambda o: -o.games)
    report.deviations = sorted((d for d in devs.values() if d.games >= MIN_GAMES), key=lambda d: (-d.games, d.move_number))
    return report


# ---------- one game, move by move (Game Review) ----------

@dataclass
class PrepPath:
    in_prep: int                 # half-moves played inside your prep
    reason: str                  # "you" / "opponent" left it, "ended" (your prep stops), "none" (no prep), "still"
    played_san: str = ""         # the move that left it
    prep_sans: List[str] = field(default_factory=list)
    prep_ucis: List[str] = field(default_factory=list)

    @property
    def departure_ply(self) -> Optional[int]:
        """Index of the move that left your prep (you or them), if one did."""
        return self.in_prep if self.reason in ("you", "opponent") else None


def repertoire_for(conn: sqlite3.Connection, color: str) -> Dict[str, Set[str]]:
    return _repertoire(conn, color)[0]


def prep_path(rep_moves: Dict[str, Set[str]], moves: List[chess.Move], mine: chess.Color) -> PrepPath:
    """Where a game leaves your prep (your repertoire for your colour), and who left it."""
    board = chess.Board()
    for ply, move in enumerate(moves[:MAX_PLY]):
        prep = rep_moves.get(position_key(board.fen()))
        if not prep:
            return PrepPath(ply, "ended" if ply else "none")
        if move.uci() not in prep:
            ucis = sorted(prep)
            return PrepPath(ply, "you" if board.turn == mine else "opponent", board.san(move),
                            [board.san(chess.Move.from_uci(u)) for u in ucis], ucis)
        board.push(move)
    return PrepPath(min(len(moves), MAX_PLY), "still")
