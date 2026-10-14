# Piece images

`cburnett/<size>/` holds the "cburnett" chess piece set by
[Colin M.L. Burnett](https://en.wikipedia.org/wiki/User:Cburnett), licensed
[GPLv2+](https://www.gnu.org/licenses/gpl-2.0.txt), taken from the SVGs in
[lichess-org/lila](https://github.com/lichess-org/lila/tree/master/public/piece/cburnett).

They're pre-rendered as PNGs at every square size the board uses (40–64 px,
see `board_widget.py`), so the app needs no SVG library — Tk loads PNGs itself.
