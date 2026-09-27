"""
Desktop GUI for the chess prep app. Two tabs:

  1. Puzzle Review — import your chess.com games, analyze them with
     Stockfish (results cached per game), and drill the mistakes it finds.
  2. Opening Drill — import a PGN repertoire and get tested on it, with
     the computer randomly playing the opponent's side and correcting
     you when you deviate from your prep.

Run: python gui.py
Requires: pip install -r requirements.txt
Requires: a local Stockfish binary for tab 1 (auto-detected, see
engine_locator.py, or set STOCKFISH_PATH manually)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import db
import theme
from board_widget import BOARD_PIXELS
from drill_tab import OpeningDrillTab
from puzzle_tab import PuzzleReviewTab
from stats_tab import StatsTab


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chess Prep")
        # tall enough for the board card (board + status/explanation +
        # buttons) without clipping, but never taller than the screen
        width = BOARD_PIXELS + 440
        # (screen height minus room for the taskbar and title bar)
        height = min(BOARD_PIXELS + 400, self.winfo_screenheight() - 110)
        self.geometry(f"{width}x{height}+40+10")
        self.minsize(BOARD_PIXELS + 360, min(BOARD_PIXELS + 200, height))

        theme.apply_theme(self)

        self.conn = db.get_connection()

        header = ttk.Frame(self, padding=(20, 16, 20, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Chess Prep", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            header, text="Review your mistakes. Drill your openings.",
            style="Muted.TLabel",
        ).pack(anchor="w")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=(6, 12))

        puzzle_tab = PuzzleReviewTab(notebook, self.conn)
        drill_tab = OpeningDrillTab(notebook, self.conn)

        notebook.add(puzzle_tab, text="Puzzle Review")
        notebook.add(drill_tab, text="Opening Drill")
        def drill_opening(opening):
            notebook.select(drill_tab)
            drill_tab.drill_suggestion(opening.name, opening.color, opening.moves)

        notebook.add(StatsTab(notebook, self.conn, on_drill=drill_opening), text="Stats")


if __name__ == "__main__":
    App().mainloop()
