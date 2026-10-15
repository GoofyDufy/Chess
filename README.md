# King Coach — desktop chess trainer

Learn from your own games: King Coach imports your chess.com games, finds
your mistakes with Stockfish and turns them into puzzles, drills your
opening repertoire (against moves real players make, from Lichess), and
keeps score with XP and levels.

## Setup

1. Install the Python packages (a virtual environment is recommended):
   ```
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip ...
   ```
   On Linux you may also need Tk: `sudo apt install python3-tk`.

2. Get Stockfish (it isn't included in this repo). The app looks for it in
   this order:
   - the `STOCKFISH_PATH` environment variable;
   - a `stockfish/` folder inside this project — download the build for
     your processor from https://stockfishchess.org/download/ (the AVX2 build
     of a modern PC is about twice as fast as a generic one) and unzip it there;
   - `/usr/local/bin`, `/opt/homebrew/bin`, `/usr/bin`, or anything on `PATH`
     (e.g. `brew install stockfish`, `sudo apt install stockfish`).

3. Optional — connect Lichess for real-world opening data: create a free
   personal access token at https://lichess.org/account/oauth/token (no
   permissions needed) and paste it in **Opening Drill → Add lines →
   Connect Lichess...**.

## Run

```
.venv/bin/python gui.py
```

## Getting started

1. **Import & Analyze** → **Import games from chess.com**: enter your
   username; it imports the last 12 months by default (0 = all history).
2. **Analyze new games**: Stockfish checks every move of yours, newest games
   first, about 5 seconds a game with **Use both CPU cores** on. Each game is
   saved as soon as it's done, so **Stop** loses nothing and no game is ever
   analyzed twice. **Focus** (e.g. *All except Bullet*) limits which games.
3. Your mistakes become puzzles. **Blunder size** sets how big a mistake has
   to be; **Apply blunder size** rebuilds the puzzles instantly from the
   saved analysis. A mistake also has to cost real winning chances (Lichess's
   rule: a drop of 0.2+ on its −1..1 scale), so a blunder when the game was
   already lost — or still won anyway — isn't a puzzle. Blunder / Mistake use
   Lichess's tiers too (0.3+ / 0.2+).

## The screens

### Puzzle Review
Your own mistakes as puzzles. Click a piece, then its square.
- **Missed Sacrifice** puzzles: wherever Stockfish's best move was a sound
  piece sacrifice (2+ pawns' worth, you'd not be worse after it, you weren't
  already winning) and what you played instead cost you at least 0.1 in
  winning chances — even below your blunder size. Pink in the queue; filter
  them with the type menu. They're also rounds in Tactics & Threats →
  Sacrifice!!, which end with what you played instead and how the game went.
- A queue with filters: type (Blunder, **Left Hanging** — your move left a
  piece loose —, Missed Mate, …; Lichess puzzles add Fork, Pin, Hanging
  Piece — you win a loose piece —, …), **Weak spots**
  (puzzles you keep missing, until solved twice in a row), **Endgames**,
  sharp (one best move) vs quiet positions, and **Kind**: **Attacking** (a
  forced mate, or a forcing sequence that wins material — by Lichess's
  standard: it leaves you +2.00 or better and the first move is the only
  one that works), **Defensive** (you
  face a threat — in check, or passing would cost 1.5+ pawns — and the answer
  is quiet: no capture, check or attack, as Lichess defines a defensive
  move), **Counterattack** (you face a threat and hit back with a capture,
  check or attack), **Positional** (neither), or **Multi-step** only.
  Stockfish sorts new puzzles into kinds in the background after each
  analysis. **Random** jumps to a puzzle you haven't seen this session;
  ← / → move through the queue.
- **Tags** above the board: the puzzle's kind, type, length, sharp / quiet
  and (Lichess puzzles) every Lichess theme, motifs first. **+ Tag** (or
  **+N**) opens a tag picker listing them all: click Lichess-style tags on
  and off, or type your own and press Enter. Yours show in amber, right
  after the kind — click one to remove it. Your tags are kept
  when puzzles are rebuilt and show in the type filter as **Tag: …**.
- Forcing tactics are **multi-move**: play the line to the end (up to 4 of
  your moves) while Stockfish plays the replies. A miss gives **Try again** /
  **Show answer**; only a clean first run counts.
- After solving: the board stays where your line ended, with an
  explanation, the evaluation bar, and **◀ ▶** back through your line and on
  into Stockfish's continuation. Your record per puzzle, top right
  (✓ correct · ◐ near-best · ✗ missed). **Review game** and **Play it out** open the position elsewhere.
- **Analyze**: once a puzzle is done, go back to its starting position and
  try your own ideas — move either side, ◀ takes back, ▶ walks the solution —
  while Stockfish shows its top 3 lines (green / blue / grey arrows) and the
  evaluation bar follows. Click a line and ▶ plays it; **Stop analysis**
  returns to the solution.
- **Lichess puzzles** (optional): on Import & Analyze → **Lichess puzzles**,
  pick a rating range and how many, and **Add Lichess puzzles** pulls them
  from the free Lichess puzzle database (CC0) — only well-rated, popular
  ones; the big file is streamed, never saved. They're kept apart from your
  own: choose **My games / Lichess / My games + Lichess** in the queue's
  **Puzzles** dropdown. They show the opponent's move that sets them up, earn
  XP, and have their own records, but don't change your stats, Progress or
  weak spots from your games. **Open on Lichess** shows the original game.

### Tactics & Threats
Three modes, switched at the top of the side panel: **Tactic?**, **Threat?**
and **Sacrifice!!**.

**Sacrifice!!** — a sacrifice is only amazing if you follow it up. A
position before one of your own !! amazing moves (where you're not worse
after it), a sacrifice you **missed** in one of your games, or a Lichess
sacrifice puzzle: **find the sacrifice** yourself (a
wrong try gets a hint — which piece — then Try again or Show the
sacrifice), then find each follow-up against Stockfish's best defence, until it's mate,
you're clearly winning (+3), or the position calms down (several good moves
instead of one). A miss: Try again or Show the move. Rounds with nothing to
find after the sacrifice are skipped; **+ 100 Lichess sacrifice puzzles**
adds more at your rating. +8 XP for a clean line, +3 with help. Afterwards,
**Analyze** opens an analysis board on the final position — move either
side, ◀ back to before the sacrifice to try other follow-ups, ▶ on, with
Stockfish's top 3 lines as arrows and the evaluation bar; **Back** returns
to the result. For your own sacrifices the result also shows **what you
actually played in that game**: each follow-up ✓ or ? (judged the same way,
from the game's analysis, while it still mattered — until you were clearly
winning or it calmed down), how your opponent defended, how many follow-ups
you found and how the game ended; **Your game** in Analyze walks that line.

**Is there a tactic?** A position from your games — is there a tactic?
Answer, then play.
- A tactic means a forced mate (up to 4 moves) or a **forcing** sequence —
  checks, captures, new threats — that wins material, using Lichess's
  standard: you end up +2.00 or better and only the first move works (it
  beats the second-best by 0.7 in winning chances). About 1 in 3
  positions are quiet (from move 6 on); there any move within half a pawn
  of the best is fine, in a tactic only the best line counts.
- Try again before anything is revealed; afterwards step through the line
  with an evaluation bar. Streaks, accuracy ("tactics spotted", "false
  alarms") and XP.

**What's the threat?** Many mistakes start with missing what the opponent's
last move threatens (Progress → This week shows how many of yours do). A
position from your games right after your opponent moved:
1. Name the threat: play the move *they* want to make next (you can move
   their pieces), or press **No threat** — about 1 in 4 positions have none.
   A threat is anything that would gain them 1.5+ pawns if you could pass.
2. Answer it with your move (within half a pawn of the best is good).
Afterwards: red arrow = their threat, green = the best answer, blue = what
you played in the game. +3 XP for naming it, +5 for a good answer.

### Game Review
Step through any analyzed game: evaluation graph, mistakes marked
(??/?/?!), Stockfish's better move as an arrow, "Next mistake".
- **Move badges**, on the board (a disc in the corner of the square the move
  landed on) and in the moves list, one colour each, in this priority (a
  move that's two things shows the first): **≡ inside your prep** (grey),
  **⚑ left your prep** (purple), **!! amazing move** (pink — a sound
  sacrifice: you offered a piece, 2+ pawns' worth, with the best or nearly
  the best move, when you weren't already winning; chess.com's "brilliant"
  idea), **! only move** (teal — Stockfish's best,
  and every other move would have lost at least a mistake's worth of
  winning chances), **✓ best** (green), **?! inaccuracy** (blue), **?
  mistake** (amber), **?? blunder** (red). Only your moves are analyzed, so
  your opponent's get just the prep badges.
- **!! Your amazing moves** (under the games list): every one in your
  analyzed games, newest first; pick one to jump to it.
- **Accuracy** under the title: chess.com's (for games reviewed there —
  picked up at import, also for games you imported before) and King Coach's
  (Lichess's formula on the saved analysis). They differ a few points —
  different engines and formulas.
- **Your opening prep in the game**: under the title, the named line it
  reached and where it left your repertoire — **⚑ you left it** (with your
  prep move) or **⚑ they did** (with what your prep expects), or your prep
  ran out / had nothing for it. The graph shades the part in prep with a
  line where it ended; on the position before, green arrows show your
  prep's move.
- **Analyze** (or just play a move on the board): walk through your own
  lines from the move shown — move either side; ← / → (and ⏮ ⏭) take back
  and play on, along the game's moves or a Stockfish line you clicked —
  with Stockfish's top 3 lines (green / blue / grey arrows) and the
  evaluation bar. It says whether you're still on the game's line.
  **Back to game** returns to where you were.

### Opening Drill
Your repertoire, drilled: the computer plays the other side, you play your
moves.
- **Add lines → Opening library**: every named opening and variation from
  Lichess's open dataset (about 3,900, CC0), downloaded once. Browse by
  family or search by name, ECO code or moves (`e4 c5 Nf3`); preview a line
  on the board. Each line is flagged **✓ in your repertoire** (green),
  **◐ partly** (most of its moves) or **○ not yet**, with a filter for each,
  and **Add this line…** right under the preview — or pick many
  (Ctrl/Shift-click, Select all shown) and **Add picked to repertoire**;
  each keeps its proper name, ready to drill in Setup.
  - **Sharp / Balanced / Quiet**: every line is described from facts at its
    end position — a gambit or material given for activity, tension in the
    centre or a locked one, open files, queens off, a symmetrical
    structure, Stockfish's only-one-good-move vs several-good-moves, and
    whether its main line castles the kings on opposite sides — with a note
    when it fits (or goes against) your playing style from Stats. Filter by
    character. Stockfish describes the library once in the background
    (about 7 minutes, while the window is open; a line you click goes first).
  - **Your games, per line**: every game you've played is matched to the
    deepest named line it reached (by position, so transpositions count —
    once, in the background; new games later). Each row shows **[your score
    in N games]**; filter **Played in my games** and sort **Most played /
    Played best / Played worst** (best and worst need 3+ games and don't
    over-trust a few). The preview gives your record per colour, your
    opening accuracy (pawns lost per move over your first 15 moves), opening
    blunders and the evaluation you come out of the opening with — and
    **Review these games…** opens any of them in Game Review.
- **Add lines**: import a PGN file (sidelines in parentheses become
  branches; several games in one file become separate lines), paste a line,
  turn lines on/off. **Ruy Lopez.pgn** and **Scandinavian.pgn** hold the 5
  lines you're most likely to face at your level (from Lichess data); the
  other `.pgn` files here are ready to import too.
- **Setup**: drill one repertoire, **All White openings**, or everything from
  one first move (**White · 1.d4 openings**, **Black vs 1.e4**, …); any line,
  your weak lines, or one line. With Lichess connected, lines come up as
  often as real players at your level lead you into them. **Delete…** (or
  the Delete key) removes a line.
- While drilling: **Show full line** (and step through it on the board),
  **Try again** / **Show correct move**, then **Same line** or **New random
  line**, and **Play it out** against Stockfish.
- **Plans**: the ideas behind the opening you're drilling.
- **Tree**: your repertoire as a living tree.
  - A mini chess set along the bottom; a tree grows from each first move's
    piece (1.d4, 1.c4, …). Click a piece or a move to go in — the other
    trees step aside — and the board follows. **Zoom in** shows just the
    branch you're on. The Tree opens on the repertoire you're drilling.
  - Hover snaps to the nearest move; click, ◀ ▶, or the mouse wheel to move.
  - **Zoom any time**: Ctrl + mouse wheel (at the pointer) or the **+ / −**
    buttons in the tree's corner, up to 6×; drag an empty spot to move
    around, **⟲** for the whole tree again. Moves spread apart while text and
    stars keep their size, so crowded spots become readable (the mini board
    hides while zoomed).
  - Play moves on the board to **grow a new branch**, then **Save branch**.
    Right-click: **delete** a branch, or set where an opening "starts".
  - **Lichess sidelines**: replies players at your level often choose that
    your lines don't answer — rings show where, coloured grey / amber / red
    for under 15% / 15–30% / 30%+; click one to answer it as a new branch.
  - **Stockfish's ideas**: its top 3 moves as green / blue / grey arrows with
    their lines; click a line and step through it with ▶.
  - **Stars = your own games** (circles are Lichess data), numbered with how
    many games: a **red star** where *you* left your prep (click: why, in My
    games), a **blue star** where your *opponents* did (click: a temporary
    branch with their move, to build your answer — with how you scored and
    what you usually replied). The info line names the top 3 of each.
- **My games**: your openings as you actually play them.
  - Your most played openings (from your games), with your score and whether
    you stay in your prep; pick one to filter.
  - **Where you leave your prep**: every position where your own move wasn't
    your repertoire's, how often, and what it costs by Stockfish — the
    costliest (how much worse × how often) first. Pick one: the big board
    shows it (green = your prep move, red = yours).
  - **Why**: Stockfish compares both moves side by side, plus opening
    principles checked on the board (development, moving a piece twice, an
    early queen, giving up castling, blocking your c/d/e-pawn, taking your
    knight's natural square, the centre, a bishop's scope, a loose piece,
    the king's diagonal), what your opponent's best reply does, your own
    results with each move, and how Lichess players at your level play and
    score them. When Stockfish calls them even it says so — maybe the
    repertoire should change instead. Worked out once in the background and
    saved.
  - **What works at your level**: the moves Lichess players at your rating
    choose most there (plus your prep and your move), ranked by how often
    your opponents' usual replies leave you **+0.5 or better** by Stockfish,
    then by the average evaluation after them — with how often each is
    played, how it scores, and whether it's sound. The best practical move
    gets an amber arrow when it's neither your prep nor yours. Needs Lichess
    connected; about 10 s the first time per position, then saved.
  - **Show on the tree**: opens the Tree at that position with your game move
    as a temporary branch — play on for both sides (what players at your
    level usually play there is listed), ◀ to step back, **Save branch** to
    add it to your repertoire or **Discard**.
  - **Review games…**: the games where you played it; open one in Game
    Review at that move. **Analyze**: the position on a free analysis board
    (move either side, Stockfish's evaluation).

### Stats
Results by opening, how you lose, accuracy by game phase, blunders under
time pressure, your playing style (sharp vs quiet) with openings that fit
it, and **Repertoire**: where your games leave your prep, plus popular
Lichess moves your lines don't answer (inside your openings first).

### Progress
- **Overview**: rating over time against your goal, blunders per game,
  puzzles per week.
- **Themes**: how often you solve each kind of puzzle — kind, type, phase,
  Lichess themes and your own tags — weakest first, plus how well you see
  threats and tactics. **Practice** opens Puzzle Review on that theme
  (unsolved first); **+ Lichess** adds 100 Lichess puzzles of that theme at
  your rating.
- **This week**: your last 7 days — games, rating change, your three
  costliest mistakes — ranked by how much of your winning chances they threw
  away, as Lichess judges moves, so turning +4 into −5 counts far more than
  a blunder when already lost (**Puzzle** to retry, **Review** to see the game), the
  pattern behind them (e.g. how many came when facing a threat), and what to
  practise next, one click each.

### Opponent Prep
Scout any chess.com player's openings and see whether your repertoire
covers them.

### Levels and players
Practice earns XP (puzzles, sharp-puzzle bonus, clean drill lines, clearing
weak spots, Tactics & Threats) and levels from Pawn I to King III, shown in the
sidebar. Several players can share the app: each gets their own database
(**+ New player** in the sidebar).

## Speed

- **Screens are built the first time you open them**, not at start-up. After
  that, switching between screens is instant.
- **Stats and Progress only recompute when your data changes**, for example
  after new games, analysis or puzzle attempts.
- **The scan for amazing moves and missed sacrifices is saved per game.** The
  first scan takes a few seconds, and after that only new or re-analysed games
  are scanned again.

## Tests

```
.venv/bin/python -m unittest discover tests
```

No Stockfish or network needed: engine and Lichess answers are faked.

## Standalone Windows app (no Python needed)

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Builds `dist\KingCoach\` (Stockfish bundled if a `stockfish\` folder exists;
fonts and piece images always). Zip that folder to share it; each player's
data is stored next to `KingCoach.exe`.

## Licences

Stockfish (if bundled) is GPLv3; the cburnett piece images in `pieces/` are
GPLv2+ — see `pieces/README.md`. The app icon in `icons/` (a pawn, and the
king in its shadow) uses the same cburnett pawn.

## Web version (separate project)

A web-based version of King Coach is being sketched as its own project, in
its own folder outside this repository, so the two never mix. This repo is
the desktop app only: don't add a `web/` folder (or any other project's
files) here.

## Your data stays local

`chessprep.db` holds your games, analysis, puzzles (and any Lichess puzzles
you added), your tags, repertoires, settings and your Lichess token; `profiles.json` lists the players. Both are in
`.gitignore` and must never be committed — here or in any other project
that copies them (the web version included). The `stockfish/` folder and
the `.venv/` Python environment are ignored too.

## Porting to iOS later

`models.py` and `db.py` map directly onto a SwiftData schema — same fields,
same SM-2 math in `ReviewState.record_attempt`. `analyzer.py`'s
mistake-detection logic (compare the evaluation before and after your move,
threshold the swing) carries over as-is; only the Stockfish *binding*
changes (UCI subprocess here vs. a bundled iOS build).
