"""
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

from tkinter import messagebox

import customtkinter as ctk

import db
import profiles
import theme
from board_widget import BOARD_PIXELS
from drill_tab import OpeningDrillTab
from game_review_tab import GameReviewTab
from progress_tab import ProgressTab
from puzzle_tab import PuzzleReviewTab
from scout_tab import ScoutTab
from stats_tab import StatsTab

SIDEBAR_WIDTH = 200


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("King Coach")
        theme.apply_theme(self)

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
        ctk.CTkLabel(brand, text="♞", width=34, height=34, corner_radius=10,
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
        content = self.content
        self.pages = {}
        self.nav_buttons = {}

        def review_game(game_id, fen):
            self.show_page("Game Review")
            review_tab.open_game(game_id, fen)

        puzzle_tab = PuzzleReviewTab(content, self.conn, on_review_game=review_game)
        review_tab = GameReviewTab(content, self.conn)
        drill_tab = OpeningDrillTab(content, self.conn)

        def drill_opening(opening):
            self.show_page("Opening Drill")
            drill_tab.drill_suggestion(opening.name, opening.color, opening.moves)

        stats_tab = StatsTab(content, self.conn, on_drill=drill_opening,
                             on_repertoire_changed=drill_tab._refresh_repertoire_list,
                             on_review_game=review_game)
        progress_tab = ProgressTab(content, self.conn)
        scout_tab = ScoutTab(content, self.conn)

        for name, page in (("Import & Analyze", puzzle_tab.import_page),
                           ("Puzzle Review", puzzle_tab), ("Game Review", review_tab),
                           ("Opening Drill", drill_tab), ("Stats", stats_tab),
                           ("Progress", progress_tab), ("Opponent Prep", scout_tab)):
            self.pages[name] = page
            b = theme.button(self.nav_frame, name, command=lambda n=name: self.show_page(n),
                             kind="ghost", anchor="w", height=42)
            b.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[name] = b

        self.show_page("Puzzle Review")

    def show_page(self, name: str) -> None:
        for page_name, page in self.pages.items():
            if page_name == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for button_name, b in self.nav_buttons.items():
            selected = button_name == name
            b.configure(fg_color=theme.CONTROL_BG if selected else "transparent",
                        text_color=theme.TEXT if selected else theme.TEXT_SOFT,
                        font=theme.FONT_BOLD if selected else theme.FONT_BASE)


if __name__ == "__main__":
    App().mainloop()
