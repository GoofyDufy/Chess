"""The "Tactics & Threats" page: Spot the Tactic and What's the threat? as two
modes of one page (the sidebar has no room for another entry at 768 px).
The switch sits at the top of each mode's side panel; the mode is remembered."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import db
import theme
from spot_tab import SpotTab
from threat_tab import ThreatTab

TACTIC, THREAT = "Is there a tactic?", "What's the threat?"


class TrainerPage(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        saved = db.get_setting(conn, "trainer_mode")
        self.mode_var = tk.StringVar(value=saved if saved in (TACTIC, THREAT) else TACTIC)
        self.spot = SpotTab(self, conn, on_review_game=on_review_game, mode_switch=self._switch)
        self.threat = ThreatTab(self, conn, on_review_game=on_review_game, mode_switch=self._switch)
        self.show_mode(self.mode_var.get())

    def _switch(self, parent) -> ctk.CTkSegmentedButton:
        return ctk.CTkSegmentedButton(
            parent, values=[TACTIC, THREAT], variable=self.mode_var, command=self.show_mode,
            font=theme.FONT_BOLD, height=32, fg_color=theme.PANEL_ALT, selected_color=theme.BORDER_STRONG,
            selected_hover_color=theme.BORDER_STRONG, unselected_color=theme.PANEL_ALT,
            unselected_hover_color=theme.CONTROL_BG, text_color=theme.TEXT_SOFT,
            text_color_disabled=theme.TEXT_MUTED)

    def show_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        db.set_setting(self.conn, "trainer_mode", mode)
        shown, hidden = (self.spot, self.threat) if mode == TACTIC else (self.threat, self.spot)
        hidden.pack_forget()
        shown.pack(fill="both", expand=True)
