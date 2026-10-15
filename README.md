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
   saved analysis.

## The screens

### Puzzle Review
Your own mistakes as puzzles. Click a piece, then its square.
- A queue with filters: type (Hanging Piece, Fork, …), **Weak spots**
  (puzzles you keep missing, until solved twice in a row), **Endgames**, and
  sharp (one best move) vs quiet positions. **Random** jumps to a puzzle you
  haven't seen this session; ← / → move through the queue.
- Forcing tactics are **multi-move**: play the line to the end (up to 4 of
  your moves) while Stockfish plays the replies. A miss gives **Try again** /
  **Show answer**; only a clean first run counts.
- After solving: an explanation, the evaluation bar, and **◀ ▶** through
  Stockfish's main line. Your record per puzzle (✓ correct · ◐ near-best ·
  ✗ missed). **Review game** and **Play it out** open the position elsewhere.

### Spot the Tactic
A game: a position from your games — is there a tactic? Answer, then play.
- A tactic means a forced mate (up to 4 moves) or a **forcing** sequence —
  checks, captures, new threats — that wins material. About 1 in 3
  positions are quiet (from move 6 on); there any move within half a pawn
  of the best is fine, in a tactic only the best line counts.
- Try again before anything is revealed; afterwards step through the line
  with an evaluation bar. Streaks, accuracy ("tactics spotted", "false
  alarms") and XP.

### Game Review
Step through any analyzed game: evaluation graph, mistakes marked
(??/?/?!), Stockfish's better move as an arrow, "Next mistake".

### Opening Drill
Your repertoire, drilled: the computer plays the other side, you play your
moves.
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
  - Play moves on the board to **grow a new branch**, then **Save branch**.
    Right-click: **delete** a branch, or set where an opening "starts".
  - **Lichess sidelines**: replies players at your level often choose that
    your lines don't answer — rings show where, coloured grey / amber / red
    for under 15% / 15–30% / 30%+; click one to answer it as a new branch.
  - **Stockfish's ideas**: its top 3 moves as green / blue / grey arrows with
    their lines; click a line and step through it with ▶.

### Stats
Results by opening, how you lose, accuracy by game phase, blunders under
time pressure, your playing style (sharp vs quiet) with openings that fit
it, and **Repertoire**: where your games leave your prep, plus popular
Lichess moves your lines don't answer (inside your openings first).

### Progress
Rating over time against your goal, blunders per game, puzzles per week.

### Opponent Prep
Scout any chess.com player's openings and see whether your repertoire
covers them.

### Levels and players
Practice earns XP (puzzles, sharp-puzzle bonus, clean drill lines, clearing
weak spots, Spot the Tactic) and levels from Pawn I to King III, shown in the
sidebar. Several players can share the app: each gets their own database
(**+ New player** in the sidebar).

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
GPLv2+ — see `pieces/README.md`.

## Web version (separate project)

A web-based version of King Coach is being sketched as its own project, in
its own folder outside this repository, so the two never mix. This repo is
the desktop app only: don't add a `web/` folder (or any other project's
files) here.

## Your data stays local

`chessprep.db` holds your games, analysis, puzzles, repertoires, settings
and your Lichess token; `profiles.json` lists the players. Both are in
`.gitignore` and must never be committed — here or in any other project
that copies them (the web version included). The `stockfish/` folder and
the `.venv/` Python environment are ignored too.

## Porting to iOS later

`models.py` and `db.py` map directly onto a SwiftData schema — same fields,
same SM-2 math in `ReviewState.record_attempt`. `analyzer.py`'s
mistake-detection logic (compare the evaluation before and after your move,
threshold the swing) carries over as-is; only the Stockfish *binding*
changes (UCI subprocess here vs. a bundled iOS build).
