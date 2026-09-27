"""
Thematic explainers for opening families, shown in the Opening Drill tab's
Plans view: the core idea, each side's plans, the pawn structure, and what
to watch out for. Written for club players — the big picture, not theory.

notes_for(name) matches a line or repertoire name (e.g. "Queens Gambit -
Semi-Slav Defense", "Catalan - Closed") against MATCHERS in order, so more
specific families come first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class OpeningNotes:
    title: str
    idea: str
    white: List[str]
    black: List[str]
    structure: str
    watch_out: List[str]


N = OpeningNotes

NOTES = {
    "qga": N(
        "Queen's Gambit Accepted",
        "Black takes on c4 to free their position, not to keep the pawn. White regains it "
        "easily and gets a strong center; Black aims for quick ...c5 to challenge d4.",
        ["Recapture on c4 with the bishop (Bxc4) after e3 — don't overpush to win it faster",
         "Aim for e3-e4 with a big center, or play against an isolated d-pawn after ...c5 cxd4",
         "Use the half-open c-file and the active c4 bishop against f7/e6"],
        ["Strike with ...c5 early and develop fast (...Nf6, ...e6, ...a6, ...b5 ideas)",
         "Don't try to hold c4 with ...b5 too early — it weakens the queenside",
         "Trade into an equal, open position where White's center can be attacked"],
        "Open center. Often White gets an isolated queen's pawn (IQP) after ...c5 and "
        "an exchange on d4 — active pieces for White vs a long-term target for Black.",
        ["White: 3.e4 grabs the center but 3...e5 hits back — know it before playing it",
         "Black: ...b5 to keep the pawn usually gets punished by a4"],
    ),
    "exchange_qgd": N(
        "QGD Exchange Variation",
        "White trades on d5 to fix the pawn structure early (the Carlsbad structure) and "
        "then plays a clear, long-term plan.",
        ["The minority attack: b4-b5 to create a weak pawn on c6 or d5",
         "Alternatively castle long or play f3 + e4 for a central break",
         "Bishop to d3, knight to e5 or via e2-f4; rooks to b1/c1"],
        ["Kingside counterplay: pieces toward White's king (…Ne4, …Bd6, …Qh4 ideas)",
         "Reroute the knight …Nf8-g6/e6 and get the light bishop active via f5",
         "Meet b4-b5 with …c6 x b5 or …a6 so the queenside doesn't collapse"],
        "Carlsbad: White pawns a2 b2 d4 e3, Black a7 b7 c6 d5. Half-open c-file for "
        "White, half-open e-file for Black.",
        ["White: don't rush the minority attack before development is done",
         "Black: passive defense loses — you need kingside activity"],
    ),
    "qgd": N(
        "Queen's Gambit Declined (Orthodox)",
        "Black keeps a solid pawn on d5 with ...e6 and accepts a slightly cramped position "
        "in exchange for no weaknesses. White builds pressure against d5 and the queenside.",
        ["Pin the f6 knight with Bg5 to add pressure on d5",
         "Develop Rc1, Bd3 and castle; look for cxd5 at a good moment",
         "Middlegame: minority attack, or e3-e4 when it works tactically"],
        ["Free the position with ...c5 or ...dxc4 followed by ...e5",
         "Solve the problem bishop on c8 (…b6 + …Bb7, or …dxc4 and …e5)",
         "Trade pieces with …Ne4 or …h6 + …Bxf6 ideas to ease the cramp"],
        "Classical d4/c4 vs d5/e6. The key question: can Black free the c8 bishop "
        "before White's space starts to tell?",
        ["Black: the c8 bishop stuck behind e6 is the classic long-term problem",
         "White: the Elephant Trap — with Black's knight on d7, cxd5 exd5 Nxd5?? loses a "
         "piece to …Nxd5! Bxd8 Bb4+"],
    ),
    "semi_slav": N(
        "Semi-Slav Defense",
        "Black combines ...c6 and ...e6 — a rock-solid triangle that can suddenly turn "
        "sharp with ...dxc4 and ...b5. One of the richest openings in chess.",
        ["Main choices: e3 + Bd3 (Meran), or Bg5 (very sharp Anti-Meran / Botvinnik)",
         "Aim for e3-e4 to open the center while Black's queenside expands",
         "Keep the c8 bishop locked in if you can — it's Black's worst piece"],
        ["Take on c4 and play …b5, …Bb7, …a6, …c5 (the Meran plan)",
         "Or keep the tension and break with …e5 or …c5",
         "Watch timing: the queenside pawns are strong only if supported"],
        "Triangle pawns c6-d5-e6. The center stays closed until someone breaks with "
        "e4 (White) or …c5/…e5 (Black).",
        ["Both sides: lines can get tactical fast — learn your main line concretely",
         "Black: don't delay the c8 bishop's development for too long"],
    ),
    "slav": N(
        "Slav Defense",
        "Black defends d5 with ...c6 instead of ...e6, keeping the diagonal open for the "
        "c8 bishop. Solid, with fewer weaknesses than the QGD.",
        ["Main line a4 after …dxc4 stops …b5, then regain the pawn with e3 + Bxc4",
         "Build a central e3-e4 push; knight to e5 is a common outpost",
         "Exchange Slav (cxd5 cxd5) gives a quiet, symmetrical game if you want it"],
        ["Develop the light bishop OUTSIDE the pawn chain (…Bf5 or …Bg4) before …e6",
         "…dxc4 then …Bf5, …e6, …Bb4 — pressure on the c3 knight",
         "Play for …c5 or …e5 once development is done"],
        "c6-d5 vs c4-d4. After …dxc4 the position opens; Black's pawns stay healthy.",
        ["Black: grabbing c4 and holding it with …b5 invites a4 and trouble",
         "White: don't forget …Bb4 pins once your knight is on c3"],
    ),
    "catalan": N(
        "Catalan",
        "The Queen's Gambit with a fianchettoed bishop on g2. White uses the long "
        "diagonal to put pressure on Black's queenside, often giving up a pawn on c4 for "
        "lasting pressure.",
        ["The g2 bishop is everything — keep it and aim it at b7 and the a8 rook",
         "After …dxc4, regain with Qa4+/Qc2 and Qxc4; don't hurry, Black struggles to keep it",
         "Slow central play: Rd1, Nc3 or Nbd2, e4 when ready"],
        ["Take on c4 and try to hold with …a6/…b5 — or return it for easy development",
         "Neutralize the g2 bishop: …c6 blocks the diagonal, …Bb7 challenges it",
         "Free the game with …c5 or …e5 breaks"],
        "d4/c4/g3 vs d5/e6. Queenside tension decides it: can Black develop the c8 "
        "bishop without dropping b7 or c6?",
        ["White: trading the g2 bishop cheaply kills the whole opening",
         "Black: a passive …c6 setup is solid but can be ground down in the endgame"],
    ),
    "london": N(
        "London System",
        "A setup, not a sharp opening: d4, Bf4, e3, Nf3, c3, Bd3, Nbd2 against almost "
        "anything. Solid pyramid, easy plans, low theory.",
        ["Classic kingside attack: Ne5, f4, Qf3/Rf3-h3 ideas once Black castles",
         "The Bd3 + Qc2 battery toward h7",
         "Keep the Bf4 — retreat to g3 when attacked by …Nh5 or …Bd6"],
        ["Hit b2 early with …c5 and …Qb6 — the London bishop left the queenside",
         "Challenge the Bf4 with …Bd6 or …Nh5 to trade it",
         "Queenside expansion or central …e5 break"],
        "Pyramid c3-d4-e3 vs whatever Black chooses. Solid but slightly passive — "
        "White wins by outplaying, not by the opening.",
        ["White: 2…c5 + …Qb6 is the most annoying reply — have Qb3 or Nc3 ready",
         "White: don't auto-pilot — vs a King's Indian setup, h3 and a quiet plan"],
    ),
    "scotch_gambit": N(
        "Scotch Gambit",
        "White plays 3.d4 and, instead of recapturing on d4, develops with Bc4 — offering "
        "a pawn for rapid development and pressure on f7.",
        ["Fast development and attack on f7 (Ng5 ideas, e5 push)",
         "Get the pawn back with cxd4 or Nxd4 when convenient",
         "Castle early and open lines toward Black's king"],
        ["Return the pawn to finish development — trying to keep it is dangerous",
         "…Nf6 and …d5 is the classic freeing idea",
         "Trade attacking pieces, especially the c4 bishop"],
        "Open e- and d-files early. Tactical, initiative-based.",
        ["Black: greedy moves like …b5 or …Qe7 grabs lose time",
         "White: if the attack stalls you're simply a pawn down"],
    ),
    "scotch": N(
        "Scotch Game",
        "White opens the center immediately with 3.d4, trading the e5 pawn and getting "
        "free piece play. Clear, direct, and less theory than the Ruy Lopez.",
        ["After Nxd4, use the space: Nxc6 + e5 (Mieses) or Be3 + c3 setups",
         "Pressure on Black's weak c-pawns after …bxc6",
         "Kingside space with e5 and f4 once pieces are out"],
        ["Hit d4 immediately: …Nf6 (attacking e4) or …Bc5",
         "Fight for d5 and play …d5 when possible to free the game",
         "Accept doubled c-pawns for the bishop pair and open b-file"],
        "Open center: White pawn e4 vs Black pawn d7 (after the trade). Piece activity "
        "matters more than structure.",
        ["White: 4…Qh4 (Steinitz) threatens e4 — know Nc3 or Nb5",
         "Black: don't let e5 + Qe2 bury your king in the center"],
    ),
    "italian": N(
        "Italian Game",
        "Bishops to c4/c5, then a slow buildup with c3, d3 and a long maneuvering game — "
        "the modern Giuoco Pianissimo.",
        ["Prepare d4 with c3, or play slowly: d3, Re1, Nbd2-f1-g3",
         "The a4 + Ba2 retreat keeps the c4 bishop safe",
         "Kingside play with Ng3-f5 and pressure on f7"],
        ["Mirror White's setup: …d6, …a6, …Ba7, …h6",
         "Strike in the center with …d5 when it's safe",
         "Watch for Ng5 ideas against f7 early on"],
        "Closed-ish: e4/d3 vs e5/d6. Maneuvering over tactics.",
        ["Both: a sudden d4 or …d5 break can open the game — calculate before allowing it",
         "Black: early …Nf6 allows 4.Ng5 — know the Two Knights lines"],
    ),
    "two_knights": N(
        "Two Knights Defense",
        "Black ignores the threat to f7 with 3...Nf6 and meets 4.Ng5 with 4...d5, giving a "
        "pawn for fast development and the initiative.",
        ["If you take the pawn (4.Ng5 d5 5.exd5), keep it with Bb5+ and Be2",
         "Consolidate first — Black's activity is the compensation",
         "Or avoid it with 4.d3 for a quiet Italian"],
        ["After 5…Na5 6.Bb5+ c6, trade pieces and hit the knight with …h6 and …e4",
         "Development lead + open files are the whole point",
         "Never play 5…Nxd5?! without knowing the Fried Liver"],
        "Unbalanced: White up a pawn, Black ahead in development and space.",
        ["White: greedy moves that lose time give Black a winning attack",
         "Black: 5…Nxd5 6.Nxf7!? (Fried Liver) is dangerous at club level"],
    ),
    "ruy": N(
        "Ruy Lopez",
        "White pressures the e5 pawn indirectly by attacking its defender (Bb5). Long, "
        "strategic games; the classic test of 1...e5.",
        ["The c3 + d4 plan to build a big center",
         "Maneuver Nb1-d2-f1-g3 toward the kingside",
         "Keep the light bishop on the a2-g8 diagonal (Bb3/Bc2)"],
        ["Kick the bishop with …a6 and …b5 and gain queenside space",
         "Hold e5 solidly with …d6, …Re8, …Bf8",
         "Counter with …c5 or …d5 breaks"],
        "e4/d3-d4 vs e5/d6. Slow maneuvering around the e5 point.",
        ["White: the Noah's Ark trap — …b5, …c5, …c4 can trap a bishop on b3",
         "Black: early …f6 to defend e5 weakens your king"],
    ),
    "vienna": N(
        "Vienna Game / Gambit",
        "White plays 2.Nc3 and often f4, attacking e5 like a delayed King's Gambit but "
        "with the knight already out.",
        ["f4 early to open the f-file, then fxe5 and fast development",
         "Kingside attack with Qe2/Qe1-g3, Bd3, and castling short",
         "Keep the strong pawn on e5 once it arrives"],
        ["Strike in the center with …d5 — the best answer to f4",
         "Challenge the e5 pawn with …f6 or …c5 later",
         "Develop quickly; don't grab pawns in the center"],
        "Semi-open f-file for White, open center after …d5.",
        ["White: …Bc5 + …Ng4 tricks against f2 — watch the diagonal",
         "Black: passive …d6 setups invite a strong attack"],
    ),
    "kings_gambit": N(
        "King's Gambit",
        "White gives the f-pawn on move 2 to open the f-file and build a big center. "
        "Romantic, very sharp, and still dangerous at club level.",
        ["Recover f4 later with Bxf4 or keep the initiative with d4 + Bc4",
         "Open the f-file for the rook and aim at f7",
         "Don't castle into trouble: king safety first"],
        ["Keep the pawn with …g5 (main lines) or return it for development",
         "…d5 counter-gambit ideas to open the center",
         "Watch for Qh4+ checks when White's king is exposed"],
        "Wide open f-file and a loose white king. Tactical from move 2.",
        ["White: moving the queen early or ignoring development loses",
         "Black: greedy pawn grabs without development get punished"],
    ),
    "smith_morra": N(
        "Smith-Morra Gambit",
        "Against the Sicilian, White gives a pawn (3.c3 dxc3 4.Nxc3) for fast "
        "development and open c- and d-files.",
        ["Bc4, O-O, Qe2, Rd1 — pressure on d6 and f7",
         "e5 break to open lines, and Nb5/Nd5 jumps",
         "Play fast: the compensation fades if Black consolidates"],
        ["Accept, then develop solidly with …e6, …d6, …Nf6, …Be7",
         "Don't grab more material; return it if needed",
         "Aim to trade pieces and reach an endgame a pawn up"],
        "Open c- and d-files, White ahead in development.",
        ["Black: 'Siberian trap' — …Qc7 + …Nd4 tricks can win White's queen",
         "White: without active piece play you're simply a pawn down"],
    ),
    "english": N(
        "English Opening",
        "White starts with 1.c4, controlling d5 from the side. Flexible and positional — "
        "it's often a reversed Sicilian.",
        ["Fianchetto with g3/Bg2 and control d5 and the long diagonal",
         "Queenside expansion: Rb1, b4-b5",
         "Only commit the d- and e-pawns when it suits you"],
        ["Take central space: …e5 (reversed Sicilian) or …c5 (symmetrical)",
         "Block the g2 bishop's diagonal and fight for d4",
         "Kingside play with …f5 in closed lines"],
        "Flexible; often closed with d3/e4 vs d6/e5, or open symmetrical lines.",
        ["White: allowing …d5 for free gives Black a comfortable game",
         "Black: mixing plans (…e5 and …c5) can leave holes on d5"],
    ),
    "caro_kann": N(
        "Caro-Kann Defense",
        "Black supports ...d5 with ...c6 and gets the light bishop out before ...e6. "
        "Very solid, with a good pawn structure and strong endgames.",
        ["Classical lines: space with h4-h5 against the bishop, then castle queenside",
         "Advance Variation (e5): gain space and attack on the kingside",
         "Use the extra central space; Black's position is solid but a bit passive"],
        ["Develop …Bf5/…Bg4 BEFORE …e6, then …e6, …Nd7, …Ngf6",
         "Break with …c5 to challenge White's center",
         "Aim for endgames — your structure is usually better"],
        "Black's c6-d5 (or after trades, a half-open d-file). Fewer weaknesses than the French.",
        ["Black: the bishop hunted by h4-h5 — know …h6 and …Bh7 retreats",
         "White: over-extending with pawns can leave targets for the endgame"],
    ),
    "french": N(
        "French Defense",
        "Black allows White a big center with ...e6 and ...d5, then attacks it from the "
        "sides. Closed and strategic, with clear pawn breaks.",
        ["After e5, attack on the kingside: f4, Qg4, and the space you own",
         "Support the d4 base of the pawn chain (c3, Nf3, Be3)",
         "The Bd3 + Qg4 battery vs Black's king"],
        ["Attack the base of the chain: …c5 against d4, …f6 against e5",
         "Pressure d4 with …Nc6, …Qb6, …Nf5/Ne7",
         "Solve the bad c8 bishop (…b6 + …Ba6, or trade it)"],
        "Locked chain d4-e5 vs d5-e6. White plays on the kingside, Black on the queenside.",
        ["Black: the light-squared bishop is the long-term problem",
         "White: losing the d4 pawn kills the chain and the attack"],
    ),
    "petrov": N(
        "Petrov Defense",
        "Black counterattacks e4 with 2...Nf6 instead of defending e5. Symmetrical, "
        "extremely solid — a drawing weapon at top level.",
        ["After 3.Nxe5 d6 4.Nf3 Nxe4 5.d4, play for a small, lasting edge",
         "Kick the e4 knight (Re1, c4) and develop quickly",
         "Pressure on the e-file"],
        ["Keep symmetry and trade pieces",
         "The e4 knight must be supported (…d5, …Bd6)",
         "Aim for an equal, simple middlegame"],
        "Symmetrical, open e-file.",
        ["Black: 3…Nxe4?? 4.Qe2 loses — play 3…d6 first",
         "White: don't expect much; aim for small edges"],
    ),
    "scandinavian": N(
        "Scandinavian Defense",
        "Black challenges e4 immediately with 1...d5 and recaptures with the queen. "
        "Simple setup, but Black gives up time.",
        ["Gain time on the queen with Nc3, then develop fast",
         "Use the lead in development: d4, Bc4/Bd3, and castle",
         "Push d5 or play Ne5 when Black is slow"],
        ["Queen to a5 or d6, then …c6, …Bf5/…Bg4, …e6 — a solid Caro-like setup",
         "Get the light bishop out before …e6",
         "Castle and play for equality"],
        "Black gives up the center pawn early; White has space and time.",
        ["Black: the queen can get chased — have your retreats ready",
         "White: don't overextend chasing the queen with pawns"],
    ),
    "pirc": N(
        "Pirc Defense",
        "Black lets White build a big center and plans to undermine it later with "
        "...c5 or ...e5, from a fianchetto setup.",
        ["Big center plus Be3/Qd2/f3 and a kingside pawn storm",
         "Or the aggressive Austrian Attack with f4",
         "Castle long and throw pawns at Black's king"],
        ["Fianchetto (…g6, …Bg7), then …c6/…b5 or …e5 to hit the center",
         "Counterattack on the side White castles to",
         "Timing: break before the center crushes you"],
        "White: d4/e4 (+f4); Black: d6/g6. Hypermodern and unbalanced.",
        ["Black: passive play = getting crushed by e5",
         "White: overextended pawns can become targets"],
    ),
    "najdorf": N(
        "Sicilian Najdorf",
        "The most ambitious reply to 1.e4. Black's ...a6 keeps flexibility and stops "
        "Nb5; both sides attack on opposite wings.",
        ["English Attack (Be3, f3, Qd2, O-O-O, g4) — pawn storm on the kingside",
         "Or classical Be2 lines with a slower game",
         "Knight jumps to d5 are a key idea"],
        ["Queenside play: …b5, …Bb7, …Rc8, pressure on c3/c4",
         "The …d5 break is the goal in many lines",
         "Keep …e5 or …e6 structures flexible"],
        "Open Sicilian: Black has the half-open c-file, White the d5 square and space.",
        ["Both: races — count tempi in opposite-side attacks",
         "Black: the d5 hole after …e5 must be controlled"],
    ),
    "dragon": N(
        "Sicilian Dragon",
        "Black fianchettoes on g7 aiming the bishop at White's queenside. Razor-sharp "
        "opposite-side castling races.",
        ["Yugoslav Attack: Be3, f3, Qd2, O-O-O, h4-h5 to open the h-file",
         "Trade the g7 bishop with Bh6",
         "Bc4 to control d5 and target f7"],
        ["…Rc8, …Ne5-c4, and the exchange sacrifice …Rxc3",
         "Pawn storm with …b5 and …a5",
         "Keep the g7 bishop — it's your best defender and attacker"],
        "Half-open c-file for Black; h-file battle on the kingside.",
        ["Black: losing the g7 bishop often loses the game",
         "White: slow play gives Black a crushing queenside attack"],
    ),
    "nimzo": N(
        "Nimzo-Indian Defense",
        "Black pins the c3 knight with ...Bb4, fighting for e4 and often giving up the "
        "bishop to double White's c-pawns.",
        ["Keep the bishop pair and open the position",
         "e3 + Bd3 or Qc2 lines to avoid doubled pawns",
         "Use the center to push e4"],
        ["Control e4, give up the bishop for structure (…Bxc3)",
         "Blockade: …c5, …d6, …e5 against doubled pawns",
         "Play on the light squares"],
        "Often doubled c-pawns for White vs the bishop pair.",
        ["White: allowing …Bxc3 without compensation leaves weak pawns",
         "Black: don't let White open lines for the two bishops"],
    ),
    "kid": N(
        "King's Indian Defense",
        "Black gives White a big center and plans a kingside attack with ...e5, ...f5 "
        "and a pawn storm — sharp and double-edged.",
        ["Space and queenside expansion: c5, b4 once the center closes with d5",
         "Classical Be2/O-O, or aggressive Sämisch (f3) setups",
         "Keep the kingside safe — Black's attack is the danger"],
        ["After d5: …Ne8/…Nd7, …f5, …f4, …g5 — the kingside storm",
         "Or open play with …c5 or …exd4",
         "The g7 bishop springs to life when the center opens"],
        "Closed center (d5 vs e5): race — White queenside, Black kingside.",
        ["Black: passive play loses on the queenside",
         "White: opening the kingside yourself helps Black"],
    ),
    "grunfeld": N(
        "Grunfeld Defense",
        "Black lets White build a big center with ...d5 and cxd5 Nxd5, then attacks it "
        "with the g7 bishop, ...c5 and piece pressure.",
        ["Big center e4/d4 and keep it supported (Be3, Rc1, Nf3)",
         "Push d5 when it gains space safely",
         "Queenside endgames can favor White's center"],
        ["Hit d4: …c5, …Nc6, …Bg4, …Qa5",
         "The g7 bishop is the key piece",
         "Trade the center pawns and play against weaknesses"],
        "White's big center vs Black's pressure — hypermodern.",
        ["Black: slow play lets the center roll forward",
         "White: the d4 pawn is always the target"],
    ),
    "dutch": N(
        "Dutch Defense",
        "Black plays 1...f5 to control e4 and aim for kingside play. Unbalanced and "
        "combative, with a slightly weakened king.",
        ["Target the e6 square and the weakened king (h4, Bh3 ideas)",
         "Fianchetto and fight for e4",
         "Central e4 break to open lines"],
        ["Control e4, kingside attack with …Qe8-h5 and …g5",
         "Leningrad (…g6/…Bg7) or Stonewall (…d5/…e6/…c6) setups",
         "…e5 break in the Leningrad"],
        "Black's f5 pawn gives kingside space but weakens e6 and the king.",
        ["Black: early Bg5 or h4 lines are dangerous — know them",
         "White: don't allow a free …e5"],
    ),
    "benko": N(
        "Benko Gambit",
        "Black sacrifices a queenside pawn for long-term pressure on the a- and b-files. "
        "Strategic compensation that lasts into the endgame.",
        ["Consolidate the extra pawn: safe king, pieces to the queenside",
         "Central play with e4-e5 when possible",
         "Trade pieces — each trade favors the extra pawn"],
        ["Rooks on a8/b8, bishop on g7, pressure on a2/b2",
         "Knight to b6/c4, queen to a5 — squeeze the queenside",
         "Don't rush: the pressure grows over time"],
        "Half-open a- and b-files for Black; White up a pawn.",
        ["White: passive defense gets squeezed",
         "Black: trading queens too early can leave you just a pawn down"],
    ),
    "englund": N(
        "Englund Gambit",
        "A risky gambit (1.d4 e5) that sets traps but is objectively unsound — well "
        "prepared opponents get an edge.",
        ["As White: just develop and return the pawn if needed",
         "Know the …Qb4+ trap (Bd2 Qxb2 Bc3?? Qc1#)",
         "Solid play wins against it"],
        ["Aim for quick …Qe7 + …Qb4+ traps and fast development",
         "Keep pieces active",
         "Consider a sounder defense to 1.d4 as your rating climbs"],
        "White up a pawn, Black relying on traps.",
        ["Black: good players don't fall for the traps",
         "White: don't get greedy with b2 captures"],
    ),
}

# (keywords that must ALL appear, notes key) — most specific first
MATCHERS: List[Tuple[Tuple[str, ...], str]] = [
    (("accepted",), "qga"),
    (("exchange", "queens gambit"), "exchange_qgd"),
    (("semi slav",), "semi_slav"),
    (("slav",), "slav"),
    (("orthodox",), "qgd"),
    (("queens gambit declined",), "qgd"),
    (("queens gambit",), "qgd"),
    (("catalan",), "catalan"),
    (("london",), "london"),
    (("scotch gambit",), "scotch_gambit"),
    (("scotch",), "scotch"),
    (("two knights",), "two_knights"),
    (("italian",), "italian"),
    (("giuoco",), "italian"),
    (("ruy lopez",), "ruy"),
    (("vienna",), "vienna"),
    (("kings gambit",), "kings_gambit"),
    (("smith morra",), "smith_morra"),
    (("english",), "english"),
    (("caro",), "caro_kann"),
    (("french",), "french"),
    (("petrov",), "petrov"),
    (("scandinavian",), "scandinavian"),
    (("pirc",), "pirc"),
    (("najdorf",), "najdorf"),
    (("dragon",), "dragon"),
    (("nimzo",), "nimzo"),
    (("kings indian",), "kid"),
    (("grunfeld",), "grunfeld"),
    (("dutch",), "dutch"),
    (("benko",), "benko"),
    (("englund",), "englund"),
]


def _normalize(name: str) -> str:
    name = name.lower().replace("'", "").replace("’", "").replace("ü", "u")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def notes_for(*names: Optional[str]) -> Optional[OpeningNotes]:
    """First match across the given names (e.g. line name, then repertoire name)."""
    for name in names:
        if not name:
            continue
        text = _normalize(name)
        for keywords, key in MATCHERS:
            if all(k in text for k in keywords):
                return NOTES[key]
    return None
