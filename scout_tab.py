"""Opponent Prep: scout any chess.com player's openings before you play
them — what they play as White and as Black, how they score, and whether
your own repertoire covers it. Their games are downloaded into memory
only (never saved to your database)."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import customtkinter as ctk

import chess_com_client
import scouting
import theme
from models import TIME_FILTERS



class ScoutTab(ctk.CTkFrame):
    def __init__(self, master, conn):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._games = []
        self._username = ""
        self._build()
        self.after(100, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return   # tab destroyed (player switched)
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _build(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=24, pady=20)
        top = ctk.CTkFrame(outer, fg_color="transparent")
        top.pack(fill="x")
        theme.label(top, "Opponent Prep", "title").pack(side="left")

        controls = ctk.CTkFrame(outer, fg_color="transparent")
        controls.pack(fill="x", pady=(12, 0))
        theme.label(controls, "chess.com username", "muted").pack(side="left")
        self.user_var = tk.StringVar()
        entry = theme.entry(controls, self.user_var, width=180)
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda e: self._scout())
        theme.label(controls, "months", "muted").pack(side="left", padx=(8, 0))
        self.months_var = tk.StringVar(value="6")
        theme.entry(controls, self.months_var, width=48).pack(side="left", padx=8)
        self.scout_button = theme.button(controls, "Scout player", self._scout, "primary", width=130)
        self.scout_button.pack(side="left", padx=(8, 0))
        self.filter_var = tk.StringVar(value="All games")
        theme.option_menu(controls, list(TIME_FILTERS), self.filter_var, lambda _: self._render(),
                          width=150).pack(side="right")

        self.status_label = theme.label(outer, "Enter an opponent's username to see how they open.",
                                        "muted", wraplength=900)
        self.status_label.pack(anchor="w", pady=(8, 8))

        grid = ctk.CTkFrame(outer, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1)
        grid.rowconfigure((0, 1), weight=1, uniform="row")
        self.white_tree = self._table(grid, 0, "When they have White")
        self.black_tree = self._table(grid, 1, "When they have Black")

    def _table(self, parent, row: int, title: str):
        from tkinter import ttk
        card = theme.card(parent)
        card.grid(row=row, column=0, sticky="nsew", pady=(0, 7) if row == 0 else (7, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        theme.label(inner, title, "heading").pack(anchor="w", pady=(0, 6))
        body = ctk.CTkFrame(inner, fg_color="transparent")
        body.pack(fill="both", expand=True)
        cols = [("line", "Line (first 3 moves each)", 360), ("games", "Games", 70),
                ("wdl", "Their W / D / L", 120), ("score", "Their score", 90),
                ("prep", "Your prep", 110)]
        tree = ttk.Treeview(body, columns=[c[0] for c in cols], show="headings", height=6)
        for key, heading, width in cols:
            tree.heading(key, text=heading)
            tree.column(key, width=width, anchor="w" if key == "line" else "center", stretch=key == "line")
        scroll = ctk.CTkScrollbar(body, command=tree.yview, button_color=theme.CONTROL_BG, fg_color="transparent")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        return tree

    # ---------- fetching (background thread, UI via queue) ----------

    def _scout(self) -> None:
        username = self.user_var.get().strip()
        if not username:
            return
        try:
            months = max(1, int(self.months_var.get()))
        except ValueError:
            months = 6
        self.scout_button.configure(state="disabled")
        self.status_label.configure(text=f"Downloading {username}'s games...", text_color=theme.TEXT_MUTED)

        def progress(done, total):
            self._ui_queue.put(lambda: self.status_label.configure(
                text=f"Downloading {username}'s games — month {done} of {total}..."))

        def worker():
            try:
                games = chess_com_client.fetch_all_games(username, months, progress)
                self._ui_queue.put(lambda: self._on_fetched(username, games))
            except Exception as e:
                msg = str(e)
                self._ui_queue.put(lambda: self._on_error(username, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_error(self, username: str, msg: str) -> None:
        self.scout_button.configure(state="normal")
        self.status_label.configure(text=f"Couldn't download {username}'s games: {msg}", text_color=theme.ERROR)

    def _on_fetched(self, username: str, games) -> None:
        self.scout_button.configure(state="normal")
        self._username, self._games = username, games
        self._render()

    # ---------- analysis ----------

    def _render(self) -> None:
        if not self._games:
            return
        data = scouting.scout_lines(self.conn, self._games, TIME_FILTERS[self.filter_var.get()])
        for their_color, tree in (("white", self.white_tree), ("black", self.black_tree)):
            tree.delete(*tree.get_children())
            for row in data[their_color]:
                tree.insert("", "end", values=(
                    row["line"], row["games"], f"{row['wins']} / {row['draws']} / {row['losses']}",
                    f"{100 * row['score']:.0f}%",
                    "✓ covered" if row["covered_by_your_repertoire"] else "not covered",
                ))
        self.status_label.configure(
            text=f"{self._username}: {data['games']} games in this filter. Lines played at least "
                 f"{scouting.MIN_GAMES} times. 'Your prep' checks your own repertoire for the other color.",
            text_color=theme.TEXT_MUTED)
