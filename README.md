# King Coach — desktop prototype

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Get Stockfish (only needed for Puzzle Review / Stats analysis — it isn't
   included in this repo):
   - Windows/Linux: download from https://stockfishchess.org/download/ and
     unzip it into a `stockfish/` folder inside this project. The app finds
     it automatically.
   - macOS: `brew install stockfish` (also found automatically).
   - Or set the `STOCKFISH_PATH` environment variable to the binary's path.

## Run

```
python gui.py
```

## Workflow

1. Click **Import games from chess.com** and enter your username — pulls
   your last 3 months of games into the local SQLite database
   (`chessprep.db`, created automatically next to the code).
2. Click **Analyze new games** — runs Stockfish on up to "Games per run"
   games (newest first), roughly 5–10 seconds per game at the default
   `ANALYSIS_DEPTH`. Each game's raw engine output is saved to the
   `move_evaluations` table as soon as it finishes, so a game is never
   analyzed twice, and **Stop** keeps every finished game.
3. Change **Blunder size** and click **Apply blunder size (instant)** to
   rebuild puzzles from the saved analysis — no Stockfish involved.
   (Games analyzed before this cache existed keep their old puzzles and
   are re-analyzed once, after new games, by Analyze new games.)
4. Puzzles appear in a randomly ordered queue. Click a piece, then a
   destination square. On a miss you can **Try again** or **Show answer**;
   the ← / → arrow keys move between puzzles.

## Opening Drill tab

The **Opening Drill** tab lets you import a repertoire PGN and get quizzed
on it directly:

You have two ways to get lines in — neither depends on your own games:

1. **Import PGN file...** — pick a `.pgn` file, name the repertoire (e.g.
   "White: 1.e4"), and say whether you're drilling white or black.
   - The PGN can contain variations (sidelines in parentheses) to encode
     branches — most repertoire-builder / lichess study PGN exports work
     as-is.
   - You can also just paste several separate single-line PGNs into one
     file; each becomes its own line in the tree.
2. **Paste / type a line...** — no file needed. Type moves directly, e.g.
   `1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6`, name it (e.g. "Ruy Lopez"),
   and pick a color. Sidelines in parentheses work here too, e.g.
   `2. Nf3 (2. Nc3 Nc6)`.
3. Pick the repertoire from the dropdown and click **Start drill**. The
   board is shown from your side (flipped when drilling Black).
4. The computer plays the opponent's moves and waits for yours. Only the
   drilled line's own next move counts; on a miss, **Try again** or
   **Show correct move**.
5. Click **New random line** any time for a fresh line from the same
   repertoire.

## More screens

- **Game Review** — step through any analyzed game: evaluation graph, your
  mistakes marked (??/?/?!), Stockfish's better move as an arrow, and
  "Next mistake". Also opens from a puzzle's **Review game** button.
- **Play it out** — play any puzzle position (or the end of a drilled
  opening line) against Stockfish at an adjustable strength. With the
  puzzle queue's **Endgames** filter this doubles as an endgame trainer.
- **Weak spots** — puzzles and opening lines you miss come back (puzzle
  filter "Weak spots", drill option "Weak lines") until you solve them
  twice in a row.
- **Stats** — weaknesses, playing style (sharp vs quiet) with opening
  recommendations, and **Repertoire**: where your games leave your prep.
- **Progress** — rating over time against your goal, blunders per game,
  puzzles per week.
- **Opponent Prep** — scout any chess.com player's openings and see
  whether your repertoire covers them.

## Tests

```
python -m unittest discover tests
```

## Standalone app (no Python needed)

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Builds `dist\KingCoach\` (Stockfish and fonts bundled if a `stockfish\`
folder exists). Zip that folder to share it; each player's data is stored
in `chessprep.db` next to `KingCoach.exe`.

## Not yet built (left for later)

- Puzzle difficulty tuning beyond the flat `MISTAKE_THRESHOLD_CP` constant.
- Any UI for browsing puzzle history / stats.
- Drill-specific spaced repetition (currently opening drill is pure random
  walk each time, not scheduled like the mistake puzzles).

## Porting to iOS later

`models.py` and `db.py` map directly onto the SwiftData schema from
earlier — same fields, same SM-2 math in `ReviewState.record_attempt`.
`analyzer.py`'s mistake-detection logic (compare eval before/after your
move, threshold the swing) is the part worth carrying over as-is; only the
Stockfish *binding* changes (UCI subprocess here vs. a bundled iOS build).
