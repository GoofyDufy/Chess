""""
Desktop GUI for the King Coach app. Three screens, picked from a sidebar:

  1. Puzzle Review — import your chess.com games, analyze them with
     Stockfish (results cached per game), and drill the mistakes it finds.
  2. Opening Drill — import a PGN repertoire and get tested on it, with
     the computer randomly playing the opponent's side and correcting
     you when you deviate from your prep.
  3. Stats — weaknesses, playing style, and openings that fit it.
  4. Game Review, Progress (rating vs goal), Opponent Prep (scout a player).

Run: python gui.py
Requires: pip install -r requirements.txt
Requires: a local Stockfish binary for analysis (auto-detected, see
engine_locator.py, or set STOCKFISH_PATH manually)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import db
import engine_service
import paths
import profiles
import progression
import theme
from board_widget import BOARD_PIXELS
from drill_tab import OpeningDrillTab
from game_review_tab import GameReviewTab
from level_card import LevelCard
from progress_tab import ProgressTab
from puzzle_tab import PuzzleReviewTab
from scout_tab import ScoutTab
from trainer_page import THREAT, TACTIC, TrainerPage
from stats_tab import StatsTab

SIDEBAR_WIDTH = 200


class _LazyPages(dict):
    """name -> page; a page not built yet is built (by its factory) when first asked for."""
    factories: dict = {}

    def __missing__(self, name):
        page = self.factories[name]()
        self[name] = page
        return page


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("King Coach")
        # the shared Stockfish (engine_service.py) goes with the window
        engine_service.reopen()
        self.bind("<Destroy>", lambda e: engine_service.shutdown() if e.widget is self else None, add="+")
        theme.apply_theme(self)
        # the logo — a pawn and the king in its shadow (icons/): window / taskbar icon and the sidebar
        icons = paths.resource_dir() / "icons"
        try:
            self._icons = [tk.PhotoImage(file=str(icons / f"kingcoach-{n}.png")) for n in (64, 48, 34)]
            self.iconphoto(True, *self._icons[:2])
        except tk.TclError:
            self._icons = []

        # tall enough for the board card (board + status + buttons) without
        # clipping, but never taller than the screen (minus taskbar/title bar)
        width = SIDEBAR_WIDTH + BOARD_PIXELS + 520
        height = min(BOARD_PIXELS + 300, self.winfo_screenheight() - 110)
        self.geometry(f"{width}x{height}+30+10")
        self.minsize(SIDEBAR_WIDTH + BOARD_PIXELS + 440, min(BOARD_PIXELS + 200, height))

        self.conn = None

        sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=theme.SIDEBAR_BG)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=theme.BORDER).pack(side="left", fill="y")

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(24, 22))
        if self._icons:
            tk.Label(brand, image=self._icons[2], bg=theme.SIDEBAR_BG, bd=0, highlightthickness=0).pack(side="left")
        else:                                  # icons/ missing: the king glyph
            ctk.CTkLabel(brand, text="♚", width=34, height=34, corner_radius=10,
                         fg_color=theme.ACCENT, text_color=theme.ACCENT_TEXT,
                         font=(theme.FONT_FAMILY, 20)).pack(side="left")
        theme.label(brand, "King Coach", "heading").pack(side="left", padx=(10, 0))

        # ---- player switcher (bottom of the sidebar) ----
        player_card = theme.card(sidebar, fg_color=theme.PANEL_BG)
        player_card.pack(side="bottom", fill="x", padx=12, pady=16)
        theme.label(player_card, "PLAYER", "section").pack(anchor="w", padx=12, pady=(10, 4))
        self.player_var = ctk.StringVar(value=profiles.current())
        self.player_menu = theme.option_menu(player_card, profiles.list_profiles(), self.player_var,
                                             self._on_player_selected, width=150)
        self.player_menu.pack(padx=12, fill="x")
        theme.button(player_card, "+ New player", self._new_player, "ghost", height=32).pack(
            fill="x", padx=6, pady=(4, 8))

        # ---- level / XP (just above the player switcher) ----
        self.level_card = LevelCard(sidebar)
        self.level_card.pack(side="bottom", fill="x", padx=12)

        self.nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.nav_frame.pack(fill="x")
        self.content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True)

        self._open_profile(profiles.current())

    # ---------- player profiles ----------

    def _open_profile(self, name: str) -> None:
        """Each player has their own database; switching rebuilds every
        screen on the new one so nothing from the other player shows."""
        if self.conn is not None:
            self.conn.close()
        for child in self.content.winfo_children() + self.nav_frame.winfo_children():
            child.destroy()
        # tabs bind the arrow keys on the window; drop the old tabs' bindings
        self.unbind("<Left>")
        self.unbind("<Right>")

        profiles.set_current(name)
        self.player_var.set(name)
        self.title(f"King Coach — {name}")
        self.conn = db.get_connection(profiles.db_path(name))
        progression.clear_listeners()   # the old player's tabs and card are gone
        self.level_card.attach(self.conn)
        self._build_pages()

    def _on_player_selected(self, name: str) -> None:
        if name == profiles.current():
            return
        puzzle_tab = self.pages.get("Puzzle Review")
        if puzzle_tab is not None and puzzle_tab._busy:
            messagebox.showinfo("Busy", "Wait for the current import or analysis to finish "
                                        "(or press Stop) before switching players.", parent=self)
            self.player_var.set(profiles.current())
            return
        self._open_profile(name)

    def _new_player(self) -> None:
        dialog = ctk.CTkInputDialog(title="New player",
                                    text="Player name (their chess.com username works best):")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        try:
            name = profiles.add(name)
        except ValueError as e:
            messagebox.showerror("New player", str(e), parent=self)
            return
        self.player_menu.configure(values=profiles.list_profiles())
        puzzle_tab = self.pages.get("Puzzle Review")
        if puzzle_tab is not None and puzzle_tab._busy:
            messagebox.showinfo("Player added", f"'{name}' was added. Switch to them once the "
                                                "current analysis finishes.", parent=self)
            return
        self._open_profile(name)
        # prefill their username so Import only needs a click
        db.set_setting(self.conn, "chess_com_username", name)
        if messagebox.askyesno("New player", f"Import {name}'s games from chess.com now?", parent=self):
            self.show_page("Import & Analyze")
            self.pages["Puzzle Review"]._import_games()

    def _build_pages(self) -> None:
        """Puzzle Review (the first page, which also runs the background puzzle
        sorting) and Import & Analyze are built now; every other page the first
        time it's opened or linked to — building them all up front took most of
        the startup time."""
        content = self.content
        self.nav_buttons = {}
        pages = self.pages = _LazyPages()

        def review_game(game_id, fen):
            self.show_page("Game Review")
            pages["Game Review"].open_game(game_id, fen)

        def drill_opening(opening):
            self.show_page("Opening Drill")
            pages["Opening Drill"].drill_suggestion(opening.name, opening.color, opening.moves)

        def repertoire_changed():
            if "Opening Drill" in pages:                 # not built yet: it reads the lines when it is
                pages["Opening Drill"]._refresh_repertoire_list()

        def go(page, then=None):
            self.show_page(page)
            if then:
                then()

        def open_puzzle(puzzle_id):
            self.show_page("Puzzle Review")
            pages["Puzzle Review"].open_puzzle(puzzle_id)

        puzzle_tab = PuzzleReviewTab(content, self.conn, on_review_game=review_game)
        pages["Puzzle Review"] = puzzle_tab
        pages["Import & Analyze"] = puzzle_tab.import_page
        pages.factories = {
            "Tactics & Threats": lambda: TrainerPage(content, self.conn, on_review_game=review_game),
            "Game Review": lambda: GameReviewTab(content, self.conn),
            "Opening Drill": lambda: OpeningDrillTab(content, self.conn, on_review_game=review_game),
            "Stats": lambda: StatsTab(content, self.conn, on_drill=drill_opening,
                                      on_repertoire_changed=repertoire_changed, on_review_game=review_game),
            "Progress": lambda: ProgressTab(content, self.conn, actions={
                "practice_theme": lambda tag: go("Puzzle Review", lambda: puzzle_tab.practice_theme(tag)),
                "open_puzzle": open_puzzle,
                "review_game": review_game,
                "threats": lambda: go("Tactics & Threats", lambda: pages["Tactics & Threats"].show_mode(THREAT)),
                "tactics": lambda: go("Tactics & Threats", lambda: pages["Tactics & Threats"].show_mode(TACTIC)),
                "analyze": lambda: go("Import & Analyze"),
            }),
            "Opponent Prep": lambda: ScoutTab(content, self.conn),
        }

        for name in ("Import & Analyze", "Puzzle Review", "Tactics & Threats", "Game Review",
                     "Opening Drill", "Stats", "Progress", "Opponent Prep"):
            b = theme.button(self.nav_frame, name, command=lambda n=name: self.show_page(n),
                             kind="ghost", anchor="w", height=36)   # 8 fit above the level card at 768 px
            b.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[name] = b

        self.show_page("Puzzle Review")

    def show_page(self, name: str) -> None:
        shown = self.pages[name]                         # built now if it's the first visit
        for page in list(self.pages.values()):
            if page is not shown:
                page.pack_forget()
        shown.pack(fill="both", expand=True)
        for button_name, b in self.nav_buttons.items():
            selected = button_name == name
            b.configure(fg_color=theme.CONTROL_BG if selected else "transparent",
                        text_color=theme.TEXT if selected else theme.TEXT_SOFT,
                        font=theme.FONT_BOLD if selected else theme.FONT_BASE)


if __name__ == "__main__":
    try:
        App().mainloop()
    finally:
        engine_service.shutdown()
