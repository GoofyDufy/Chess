"""
Hand-curated openings tagged by character, used to recommend openings
that fit your playing style (style.py). Each has one representative main
line that "Drill this" imports into the Opening Drill tab.

`match` is a lowercase fragment of chess.com's opening name (as produced
by stats.opening_name) used to find your own record with that opening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from models import PlayerColor
from stats import Record

SHARP, BALANCED, POSITIONAL = "Sharp", "Balanced", "Positional"


@dataclass(frozen=True)
class CatalogOpening:
    name: str
    color: PlayerColor
    character: str
    match: str
    moves: str
    note: str


W, B = PlayerColor.WHITE, PlayerColor.BLACK

CATALOG: List[CatalogOpening] = [
    # ---- White ----
    CatalogOpening("Italian Game (Giuoco Pianissimo)", W, BALANCED, "italian game",
                   "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d3 d6 6. O-O O-O 7. Re1 a6 8. Bb3",
                   "Slow buildup, easy plans"),
    CatalogOpening("Ruy Lopez (Closed)", W, POSITIONAL, "ruy lopez",
                   "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3 O-O 9. h3",
                   "Long-term pressure, rich maneuvering"),
    CatalogOpening("Scotch Game", W, SHARP, "scotch game",
                   "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 Nf6 5. Nxc6 bxc6 6. e5 Qe7 7. Qe2 Nd5 8. c4",
                   "Opens the center early"),
    CatalogOpening("Vienna Gambit", W, SHARP, "vienna game",
                   "1. e4 e5 2. Nc3 Nf6 3. f4 d5 4. fxe5 Nxe4 5. Nf3 Be7 6. d4 O-O 7. Bd3",
                   "Kingside attack chances"),
    CatalogOpening("King's Gambit", W, SHARP, "kings gambit",
                   "1. e4 e5 2. f4 exf4 3. Nf3 g5 4. h4 g4 5. Ne5 Nf6 6. d4 d6 7. Nd3 Nxe4 8. Bxf4",
                   "Gambit play, very tactical"),
    CatalogOpening("Smith-Morra Gambit", W, SHARP, "smith morra",
                   "1. e4 c5 2. d4 cxd4 3. c3 dxc3 4. Nxc3 Nc6 5. Nf3 d6 6. Bc4 e6 7. O-O Nf6 8. Qe2 Be7 9. Rd1",
                   "Pawn for fast development vs the Sicilian"),
    CatalogOpening("Queen's Gambit", W, POSITIONAL, "queens gambit",
                   "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 5. e3 O-O 6. Nf3 h6 7. Bh4 b6",
                   "Classical central control"),
    CatalogOpening("London System", W, POSITIONAL, "london system",
                   "1. d4 d5 2. Bf4 Nf6 3. e3 e6 4. Nf3 c5 5. c3 Nc6 6. Nbd2 Bd6 7. Bg3 O-O 8. Bd3",
                   "Same setup every game, low theory"),
    CatalogOpening("Catalan", W, POSITIONAL, "catalan",
                   "1. d4 Nf6 2. c4 e6 3. g3 d5 4. Bg2 Be7 5. Nf3 O-O 6. O-O dxc4 7. Qc2 a6 8. Qxc4 b5 9. Qc2 Bb7",
                   "Long-diagonal pressure"),
    CatalogOpening("English Opening", W, POSITIONAL, "english opening",
                   "1. c4 e5 2. Nc3 Nf6 3. g3 d5 4. cxd5 Nxd5 5. Bg2 Nb6 6. Nf3 Nc6 7. O-O Be7",
                   "Flexible, maneuvering play"),
    # ---- Black vs 1.e4 ----
    CatalogOpening("Caro-Kann (Classical)", B, POSITIONAL, "caro kann",
                   "1. e4 c6 2. d4 d5 3. Nc3 dxe4 4. Nxe4 Bf5 5. Ng3 Bg6 6. h4 h6 7. Nf3 Nd7 8. h5 Bh7 9. Bd3 Bxd3 10. Qxd3",
                   "Solid structure, good endgames"),
    CatalogOpening("French Defense (Steinitz)", B, POSITIONAL, "french defense",
                   "1. e4 e6 2. d4 d5 3. Nc3 Nf6 4. e5 Nfd7 5. f4 c5 6. Nf3 Nc6 7. Be3 cxd4 8. Nxd4 Bc5",
                   "Closed center, counterattack on the pawn chain"),
    CatalogOpening("Petrov Defense", B, POSITIONAL, "petrovs defense",
                   "1. e4 e5 2. Nf3 Nf6 3. Nxe5 d6 4. Nf3 Nxe4 5. d4 d5 6. Bd3 Nc6 7. O-O Be7",
                   "Very solid, symmetrical"),
    CatalogOpening("Scandinavian (3...Qa5)", B, BALANCED, "scandinavian defense",
                   "1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. d4 Nf6 5. Nf3 c6 6. Bc4 Bf5 7. Bd2 e6",
                   "Simple setup, but you give up time"),
    CatalogOpening("Two Knights Defense", B, SHARP, "two knights",
                   "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Na5 6. Bb5+ c6 7. dxc6 bxc6 8. Be2 h6 9. Nf3 e4 10. Ne5",
                   "Pawn sacrifice for activity"),
    CatalogOpening("Pirc Defense", B, SHARP, "pirc defense",
                   "1. e4 d6 2. d4 Nf6 3. Nc3 g6 4. Be3 Bg7 5. Qd2 c6 6. f3 b5",
                   "Hypermodern, counterpunching"),
    CatalogOpening("Sicilian Najdorf", B, SHARP, "sicilian defense najdorf",
                   "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e5 7. Nb3 Be6 8. f3 Be7",
                   "Unbalanced, fight for the win"),
    CatalogOpening("Sicilian Dragon", B, SHARP, "sicilian defense dragon",
                   "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 g6 6. Be3 Bg7 7. f3 O-O 8. Qd2 Nc6 9. Bc4 Bd7",
                   "Opposite-side attacks, razor sharp"),
    # ---- Black vs 1.d4 ----
    CatalogOpening("Queen's Gambit Declined", B, POSITIONAL, "queens gambit declined",
                   "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 5. e3 O-O 6. Nf3 Nbd7 7. Rc1 c6",
                   "Rock-solid classical defense"),
    CatalogOpening("Slav Defense", B, POSITIONAL, "slav defense",
                   "1. d4 d5 2. c4 c6 3. Nf3 Nf6 4. Nc3 dxc4 5. a4 Bf5 6. e3 e6 7. Bxc4 Bb4 8. O-O O-O",
                   "Solid, keeps the light bishop active"),
    CatalogOpening("Nimzo-Indian Defense", B, BALANCED, "nimzo",
                   "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 4. e3 O-O 5. Bd3 d5 6. Nf3 c5 7. O-O dxc4 8. Bxc4",
                   "Flexible, strategic imbalances"),
    CatalogOpening("King's Indian Defense", B, SHARP, "kings indian defense",
                   "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6 5. Nf3 O-O 6. Be2 e5 7. O-O Nc6 8. d5 Ne7",
                   "Kingside attack vs queenside play"),
    CatalogOpening("Grunfeld Defense", B, SHARP, "grunfeld",
                   "1. d4 Nf6 2. c4 g6 3. Nc3 d5 4. cxd5 Nxd5 5. e4 Nxc3 6. bxc3 Bg7 7. Nf3 c5 8. Be3",
                   "Attack White's big center"),
    CatalogOpening("Dutch Defense (Leningrad)", B, SHARP, "dutch defense",
                   "1. d4 f5 2. g3 Nf6 3. Bg2 g6 4. Nf3 Bg7 5. O-O O-O 6. c4 d6 7. Nc3 Qe8",
                   "Aggressive, unbalanced"),
    CatalogOpening("Benko Gambit", B, SHARP, "benko gambit",
                   "1. d4 Nf6 2. c4 c5 3. d5 b5 4. cxb5 a6 5. bxa6 Bxa6 6. Nc3 d6 7. e4 Bxf1 8. Kxf1 g6",
                   "Long-term queenside pressure for a pawn"),
]

# style -> character -> fit label (lower rank sorts first)
_FIT = {
    "sharp": {SHARP: "Great fit", BALANCED: "Good fit", POSITIONAL: "Less natural"},
    "quiet": {POSITIONAL: "Great fit", BALANCED: "Good fit", SHARP: "Less natural"},
    "balanced": {BALANCED: "Great fit", SHARP: "Good fit", POSITIONAL: "Good fit"},
}
FIT_RANK = {"Great fit": 0, "Good fit": 1, "Less natural": 2, "–": 3}


def fit(style: Optional[str], opening: CatalogOpening) -> str:
    return _FIT.get(style, {}).get(opening.character, "–")


def your_record(opening: CatalogOpening, records: Dict[tuple, Record]) -> Optional[Record]:
    """Merges your records for every chess.com opening family whose name
    contains this entry's match fragment (same color). None if you've
    never played it."""
    merged = Record()
    for (name, color), rec in records.items():
        if color == opening.color.value and opening.match in name.lower():
            merged.wins += rec.wins
            merged.draws += rec.draws
            merged.losses += rec.losses
    return merged if merged.games else None
