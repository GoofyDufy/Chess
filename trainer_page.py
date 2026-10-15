"""The "Tactics & Threats" page: Spot the Tactic, What's the threat? and Make
it work (sacrifices) as three modes of one page (the sidebar has no room for another entry at 768 px).
The switch sits at the top of each mode's side panel; the mode is remembered."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

import db
import theme
from sac_tab import SacTab
from spot_tab import SpotTab
from threat_tab import ThreatTab

TACTIC, THREAT, SACRIFICE = "Tactic?", "Threat?", "Sacrifice!!"   # short: three fit the side panel


class TrainerPage(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        saved = db.get_setting(conn, "trainer_mode")
        saved = {"Is there a tactic?": TACTIC, "What's the threat?": THREAT,
                 "Make it work": SACRIFICE}.get(saved, saved)                           # older names
        self.mode_var = tk.StringVar(value=saved if saved in (TACTIC, THREAT, SACRIFICE) else TACTIC)
        self.spot = SpotTab(self, conn, on_review_game=on_review_game, mode_switch=self._switch)
        self.threat = ThreatTab(self, conn, on_review_game=on_review_game, mode_switch=self._switch)
        self.sac = SacTab(self, conn, on_review_game=on_review_game, mode_switch=self._switch)
        self.show_mode(self.mode_var.get())

    def _switch(self, parent) -> ctk.CTkSegmentedButton:
        return ctk.CTkSegmentedButton(
            parent, values=[TACTIC, THREAT, SACRIFICE], variable=self.mode_var, command=self.show_mode,
            font=theme.FONT_BOLD, height=32, fg_color=theme.PANEL_ALT, selected_color=theme.BORDER_STRONG,
            selected_hover_color=theme.BORDER_STRONG, unselected_color=theme.PANEL_ALT,
            unselected_hover_color=theme.CONTROL_BG, text_color=theme.TEXT_SOFT,
            text_color_disabled=theme.TEXT_MUTED)

    def show_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        db.set_setting(self.conn, "trainer_mode", mode)
        shown = {TACTIC: self.spot, THREAT: self.threat, SACRIFICE: self.sac}.get(mode, self.spot)
        for tab in (self.spot, self.threat, self.sac):
            if tab is not shown:
                tab.pack_forget()
        shown.pack(fill="both", expand=True)
