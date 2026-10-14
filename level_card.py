"""Sidebar card: your level, an XP bar to the next level, and a short
'+10 XP' / 'Level up!' flash whenever practice earns XP."""

from __future__ import annotations

import customtkinter as ctk

import progression
import theme

FLASH_MS = 1800
LEVEL_UP_MS = 4000


class LevelCard(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=theme.PANEL_BG, corner_radius=theme.RADIUS)
        self.conn = None
        self._xp = 0
        self._flash_job = None

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.level_label = theme.label(top, "", "heading")
        self.level_label.pack(side="left")
        self.flash_label = ctk.CTkLabel(top, text="", font=theme.FONT_BOLD, text_color=theme.SUCCESS)
        self.flash_label.pack(side="right")

        self.bar = ctk.CTkProgressBar(self, height=8, corner_radius=4, fg_color=theme.CONTROL_BG,
                                      progress_color=theme.ACCENT)
        self.bar.pack(fill="x", padx=12)
        self.xp_label = theme.label(self, "", "muted")
        self.xp_label.pack(anchor="w", padx=12, pady=(4, 10))

    def attach(self, conn) -> None:
        """Show this player's level (called on start-up and player switch)."""
        self.conn = conn
        self._xp = progression.total_xp(conn)
        self._show(progression.level_for(self._xp))
        progression.add_listener(self.refresh)

    def refresh(self) -> None:
        if self.conn is None:
            return
        old_level = progression.level_for(self._xp)
        new_xp = progression.total_xp(self.conn)
        gained, self._xp = new_xp - self._xp, new_xp
        level = progression.level_for(new_xp)
        self._show(level)
        if level.number > old_level.number:
            self._flash("Level up!", LEVEL_UP_MS, theme.ACCENT)
        elif gained > 0:
            self._flash(f"+{gained} XP", FLASH_MS, theme.SUCCESS)

    def _show(self, level: progression.Level) -> None:
        self.level_label.configure(text=level.name)
        self.bar.set(level.progress)
        if level.xp_for_level is None:
            self.xp_label.configure(text=f"{level.total_xp} XP · top level")
        else:
            self.xp_label.configure(text=f"{level.xp_into_level} / {level.xp_for_level} XP")

    def _flash(self, text: str, ms: int, color: str) -> None:
        if self._flash_job is not None:
            self.after_cancel(self._flash_job)
        self.flash_label.configure(text=text, text_color=color)
        self._flash_job = self.after(ms, self._clear_flash)

    def _clear_flash(self) -> None:
        self._flash_job = None
        self.flash_label.configure(text="")
