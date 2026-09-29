"""Stats tab: tables that point at your weaknesses — results by opening,
how you lose, accuracy by game phase, and blunders under time pressure.
Recomputed every time the tab is shown (cheap: no engine involved)."""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional
from tkinter import ttk

import customtkinter as ctk

from tkinter import messagebox

import db
import opening_importer
import openings_catalog
import repertoire_gaps
import stats
import style
import theme

from models import TIME_FILTERS as FILTERS, PlayerColor

MIN_OPENING_GAMES = 3
REPERTOIRE_FROM_GAMES = "From my games"
ALL_FIRST_MOVES = "All first moves"
BAR_WIDTH = 12


def _bar(fraction: float) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    return "▰" * filled + "▱" * (BAR_WIDTH - filled)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "–"


class StatsTab(ctk.CTkFrame):
    def __init__(self, master, conn, on_drill=None, on_repertoire_changed=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        # on_drill(CatalogOpening): opens that opening in the Opening Drill tab
        self.on_drill = on_drill
        # called after a line is added from the Repertoire page (refreshes Opening Drill)
        self.on_repertoire_changed = on_repertoire_changed
        self._build_layout()
        self.bind("<Map>", lambda e: self.refresh())

    def _build_layout(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        top = ctk.CTkFrame(outer, fg_color="transparent")
        top.pack(fill="x")
        theme.label(top, "Stats", "title").pack(side="left")
        self.page_switch = ctk.CTkSegmentedButton(
            top, values=["Overview", "Playing style", "Repertoire"], command=self._show_stats_page,
            height=34, font=theme.FONT_BOLD, fg_color=theme.PANEL_BG,
            selected_color=theme.CONTROL_BG, selected_hover_color=theme.CONTROL_BG,
            unselected_color=theme.PANEL_BG, unselected_hover_color=theme.PANEL_ALT,
            text_color=theme.TEXT,
        )
        self.page_switch.pack(side="left", padx=(24, 0))

        self.filter_var = tk.StringVar(value=db.get_setting(self.conn, "stats_filter") or "All games")
        if self.filter_var.get() not in FILTERS:
            self.filter_var.set("All games")
        theme.option_menu(top, list(FILTERS), self.filter_var, lambda _: self.refresh(),
                          width=170).pack(side="right")
        theme.label(top, "Games", "muted").pack(side="right", padx=(0, 8))
        self.summary_label = theme.label(outer, "", "muted")
        self.summary_label.pack(anchor="w", pady=(4, 0))

        pages = ctk.CTkFrame(outer, fg_color="transparent")
        pages.pack(fill="both", expand=True, pady=(12, 0))
        grid = ctk.CTkFrame(pages, fg_color="transparent")
        style_page = ctk.CTkFrame(pages, fg_color="transparent")
        rep_page = ctk.CTkFrame(pages, fg_color="transparent")
        self._stats_pages = {"Overview": grid, "Playing style": style_page, "Repertoire": rep_page}
        self._build_style_page(style_page)
        self._build_repertoire_page(rep_page)
        self.page_switch.set("Overview")
        self._show_stats_page("Overview")

        grid.columnconfigure((0, 1), weight=1, uniform="col")
        grid.rowconfigure((0, 1), weight=1, uniform="row")

        self.openings_tree = self._section(
            grid, 0, 0, "Results by opening",
            f"Openings with {MIN_OPENING_GAMES}+ games. Click a column to sort.",
            [("opening", "Opening", 190), ("color", "As", 50), ("games", "Games", 55),
             ("wdl", "W / D / L", 85), ("score", "Score", 55), ("bar", "", 110)],
        )
        self.losses_tree = self._section(
            grid, 0, 1, "How you lose",
            "Lots of timeouts means clock trouble, not chess trouble.",
            [("tc", "Time control", 95), ("lost", "Losses", 60), ("resigned", "Resigned", 75),
             ("mated", "Mated", 60), ("timeout", "Timeout", 70), ("other", "Other", 55)],
        )
        self.phase_tree = self._section(
            grid, 1, 0, "Accuracy by game phase",
            "Average pawns lost per move (lower is better). Stockfish-analyzed games only.",
            [("phase", "Phase", 100), ("moves", "Moves", 65), ("avg", "Avg loss", 75),
             ("blunders", "Blunders", 75), ("bar", "", 110)],
        )
        self.clock_tree = self._section(
            grid, 1, 1, "Blunders vs. time left",
            f"Share of moves losing {stats.BLUNDER_CP // 100}+ pawns, by clock remaining.",
            [("clock", "Time left", 95), ("moves", "Moves", 65), ("rate", "Blunder rate", 95),
             ("bar", "", 110)],
        )

    def _build_style_page(self, page) -> None:
        verdict_card = theme.card(page, fg_color="#1F1A12", border_color="#4A3A1E")
        verdict_card.pack(fill="x")
        verdict = ctk.CTkFrame(verdict_card, fg_color="transparent")
        verdict.pack(fill="x", padx=20, pady=14)
        theme.label(verdict, "YOUR STYLE", "section", text_color=theme.ACCENT_HOVER).pack(anchor="w")
        self.verdict_label = theme.label(verdict, "", "heading", wraplength=900)
        self.verdict_label.pack(anchor="w", fill="x", pady=(2, 0))
        self.style_note_label = theme.label(verdict, "", "muted", wraplength=900)
        self.style_note_label.pack(anchor="w", fill="x", pady=(2, 0))

        grid = ctk.CTkFrame(page, fg_color="transparent")
        grid.pack(fill="both", expand=True, pady=(12, 0))
        grid.columnconfigure((0, 1), weight=1, uniform="col")
        grid.rowconfigure((0, 1), weight=1, uniform="row")

        self.indicators_tree = self._section(
            grid, 0, 0, "Tactical indicators",
            "Sharp = the engine saw one clearly best move. Everyone loses more in sharp "
            "positions, so compare these over time rather than to each other.",
            [("metric", "Measure", 220), ("value", "Value", 90), ("basis", "Based on", 110)],
        )
        self.game_types_tree = self._section(
            grid, 0, 1, "Results by game type",
            "Your score by the kind of game it turned into (sharp/quiet split at your own averages).",
            [("type", "Game type", 175), ("games", "Games", 55), ("wdl", "W / D / L", 85),
             ("score", "Score", 55), ("bar", "", 110)],
        )
        self.opening_char_tree = self._section(
            grid, 1, 0, "Your openings' character",
            f"How sharp your games with each opening get (moves 9–30). {MIN_OPENING_GAMES}+ analyzed games.",
            [("opening", "Opening", 180), ("color", "As", 50), ("games", "Games", 55),
             ("score", "Score", 55), ("character", "Character", 85)],
        )
        self.recommend_tree = self._section(
            grid, 1, 1, "Openings that fit your style",
            "Double-click one (or select it and click Drill) to practice it in Opening Drill.",
            [("opening", "Opening", 190), ("color", "As", 50), ("character", "Style", 75),
             ("fit", "Fit", 85), ("you", "You", 75)],
        )
        self.recommend_tree.bind("<Double-1>", lambda e: self._drill_selected())
        theme.button(self.recommend_tree.master.master, "Drill selected opening",
                     self._drill_selected, "primary", height=34).pack(anchor="e", pady=(8, 0))

    def _show_stats_page(self, name: str) -> None:
        for page_name, page in self._stats_pages.items():
            if page_name == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        if name == "Repertoire":
            self._refresh_repertoire()

    # ---------- Repertoire page: what you face vs what you've prepared ----------

    def _build_repertoire_page(self, page) -> None:
        note_row = ctk.CTkFrame(page, fg_color="transparent")
        note_row.pack(fill="x", pady=(0, 8))
        self.first_move_var = tk.StringVar(value=ALL_FIRST_MOVES)
        self.first_move_menu = theme.option_menu(note_row, [ALL_FIRST_MOVES], self.first_move_var,
                                                 lambda _: self._render_repertoire(), width=180)
        self.first_move_menu.pack(side="right")
        theme.label(note_row, "First move", "muted").pack(side="right", padx=(0, 8))
        self.rep_note_label = theme.label(note_row, "", "muted", wraplength=640)
        self.rep_note_label.pack(side="left", fill="x", expand=True)
        self._rep_report = None
        grid = ctk.CTkFrame(page, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1)
        grid.rowconfigure((0, 1), weight=1, uniform="row")
        self.gaps_tree = self._section(
            grid, 0, 0, "Where your games leave your repertoire",
            "The first move in each game that your prep doesn't cover, most frequent first. "
            "Select one and click Add to put it (with a suggested reply) in your repertoire.",
            [("type", "Line (first uncovered move last)", 330), ("color", "As", 55),
             ("who", "Who left", 120), ("games", "Games", 60), ("score", "Score", 60),
             ("reply", "Suggested reply", 160)],
        )
        self.common_tree = self._section(
            grid, 1, 0, "Lines you face most (first 3 moves)",
            "Your most common openings. ✓ = already covered by your repertoire.",
            [("type", "Line", 330), ("opening", "Opening", 260), ("color", "As", 55),
             ("games", "Games", 60), ("score", "Score", 60), ("rep", "In rep", 60)],
        )
        for tree in (self.gaps_tree, self.common_tree):
            theme.button(tree.master.master, "Add selected to repertoire",
                         lambda t=tree: self._add_selected_to_repertoire(t),
                         "primary", height=34).pack(anchor="e", pady=(8, 0))
        self._rep_items = {}

    def _refresh_repertoire(self) -> None:
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            r = repertoire_gaps.compute(self.conn, FILTERS[self.filter_var.get()])
        finally:
            self.configure(cursor="")
        self._rep_report = r

        # first-move filter: the moves that actually start your games, most common first
        counts = r.first_moves
        options = [ALL_FIRST_MOVES] + [f"1.{san}  ({n})" for san, n in
                                       sorted(counts.items(), key=lambda kv: -kv[1])]
        self.first_move_menu.configure(values=options)
        current = self._selected_first_move()
        if current and current not in counts:
            self.first_move_var.set(ALL_FIRST_MOVES)
        elif current:
            self.first_move_var.set(next(o for o in options if o.startswith(f"1.{current} ")))
        self._render_repertoire()

    def _selected_first_move(self) -> Optional[str]:
        """'1.e4  (1210)' -> 'e4'; None for all first moves."""
        value = self.first_move_var.get()
        if value == ALL_FIRST_MOVES or not value.startswith("1."):
            return None
        return value[2:].split()[0]

    def _render_repertoire(self) -> None:
        r = self._rep_report
        if r is None:
            return
        first = self._selected_first_move()

        def starts_with(sans: List[str]) -> bool:
            return first is None or (bool(sans) and sans[0] == first)

        for tree in (self.gaps_tree, self.common_tree):
            tree.delete(*tree.get_children())
        self._rep_items = {}

        missing = [c.capitalize() for c, has in r.has_repertoire.items() if not has]
        note = f"From the first {repertoire_gaps.MAX_PLY // 2} moves of {r.games} games."
        if missing:
            note += (f" You have no {' or '.join(missing)} repertoire yet, so those games only "
                     "appear under Lines you face most — add the most common ones from there.")
        self.rep_note_label.configure(text=note)

        gaps = [g for g in r.gaps if r.has_repertoire[g.color] and starts_with(g.prefix + [g.move])]
        for i, g in enumerate(gaps):
            iid = f"gap{i}"
            self._rep_items[iid] = g
            reply = f"{g.suggestion} ({g.suggestion_source})" if g.suggestion else (
                "–" if g.by_opponent else f"{g.move} (what you play)")
            self.gaps_tree.insert("", "end", iid=iid, values=(
                g.line, g.color.capitalize(), g.who, g.record.games,
                f"{100 * g.record.score:.0f}%", reply,
            ))
        common = [c for c in r.common if starts_with(c.sans)][:60]
        for i, c in enumerate(common):
            iid = f"common{i}"
            self._rep_items[iid] = c
            self.common_tree.insert("", "end", iid=iid, values=(
                repertoire_gaps.format_line(c.sans), c.opening, c.color.capitalize(),
                c.record.games, f"{100 * c.record.score:.0f}%", "✓" if c.in_repertoire else "",
            ))

    def _add_selected_to_repertoire(self, tree) -> None:
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Nothing selected", "Select a line in the table first.", parent=self)
            return
        item = self._rep_items[selection[0]]
        color = PlayerColor(item.color)
        if isinstance(item, repertoire_gaps.Gap):
            name = f"{item.opening} — {item.line}"
            pgn = repertoire_gaps.gap_pgn(item, name[:90])
            if pgn is None:
                messagebox.showinfo("No reply to add",
                                    "There's no suggested reply for this position yet. Analyze more "
                                    "games, or add the line by hand with Paste / type a line.", parent=self)
                return
        else:
            name = f"{item.opening} — {repertoire_gaps.format_line(item.sans)}"
            pgn = f'[Event "{name[:90]}"]\n\n{repertoire_gaps.format_line(item.sans)} *\n'

        rep = REPERTOIRE_FROM_GAMES
        added = opening_importer.import_pgn_text(pgn, rep, color, self.conn)
        messagebox.showinfo(
            "Added to repertoire" if added else "Already there",
            f"Added to '{rep}' ({color.value}). Drill it in Opening Drill, and extend it "
            "with Paste / type a line." if added else f"That line is already in '{rep}'.",
            parent=self,
        )
        if self.on_repertoire_changed:
            self.on_repertoire_changed()
        self._refresh_repertoire()

    def _drill_selected(self) -> None:
        selection = self.recommend_tree.selection()
        if selection and self.on_drill:
            self.on_drill(openings_catalog.CATALOG[int(selection[0])])

    def _refresh_style(self, classes, opening_records) -> None:
        r = style.compute_style(self.conn, classes)
        self.verdict_label.configure(text=r.verdict)
        note = f"Based on {r.games} Stockfish-analyzed games in this filter."
        if r.multipv_games < r.games:
            note += (f" {r.games - r.multipv_games} were analyzed before top-3 engine moves were "
                     "recorded — Analyze new games re-analyzes them for more accurate labels.")
        self.style_note_label.configure(text=note)

        rows = []
        for name, p in r.accuracy.items():
            rows.append((f"Avg pawns lost, {name.lower()}", f"{p.avg_loss / 100:.2f}" if p.moves else "–",
                         f"{p.moves} moves"))
            rows.append((f"Blunder rate, {name.lower()}", f"{100 * p.blunder_rate:.1f}%" if p.moves else "–",
                         f"{p.moves} moves"))
        rows.append(("Only-move positions solved", _pct(r.only_moves_found, r.only_moves),
                     f"{r.only_moves} positions"))
        rows.append(("Opponent blunders punished", _pct(r.punished, r.punish_chances),
                     f"{r.punish_chances} chances"))
        for values in rows:
            self.indicators_tree.insert("", "end", values=values)

        for name, rec in r.game_types.items():
            self.game_types_tree.insert("", "end", values=(
                name, rec.games, f"{rec.wins} / {rec.draws} / {rec.losses}",
                f"{100 * rec.score:.0f}%" if rec.games else "–", _bar(rec.score) if rec.games else "",
            ))

        analyzed = [(k, v) for k, v in r.openings.items() if v[0].games >= MIN_OPENING_GAMES]
        for (name, color), (rec, sharpness) in sorted(analyzed, key=lambda kv: -kv[1][0].games):
            self.opening_char_tree.insert("", "end", values=(
                name, color.capitalize(), rec.games, f"{100 * rec.score:.0f}%", r.character(sharpness),
            ))

        entries = list(enumerate(openings_catalog.CATALOG))
        entries.sort(key=lambda ie: (openings_catalog.FIT_RANK[openings_catalog.fit(r.style, ie[1])],
                                     ie[1].color.value != "white", ie[1].name))
        for i, o in entries:
            rec = openings_catalog.your_record(o, opening_records)
            self.recommend_tree.insert("", "end", iid=str(i), values=(
                o.name, o.color.value.capitalize(), o.character, openings_catalog.fit(r.style, o),
                f"{100 * rec.score:.0f}% ({rec.games})" if rec else "new",
            ))

    def _section(self, parent, row, col, title, help_text, columns) -> ttk.Treeview:
        frame = theme.card(parent)
        frame.grid(row=row, column=col, sticky="nsew",
                   padx=(0, 8) if col == 0 else (8, 0), pady=(0, 8) if row == 0 else (8, 0))
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        theme.label(inner, title, "heading").pack(anchor="w")
        theme.label(inner, help_text, "muted", wraplength=440).pack(anchor="w", fill="x", pady=(0, 8))

        body = ctk.CTkFrame(inner, fg_color="transparent")
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=[c[0] for c in columns], show="headings", height=6)
        for key, heading, width in columns:
            tree.heading(key, text=heading, command=lambda k=key, t=tree: self._sort(t, k))
            tree.column(key, width=width,
                        anchor="w" if key in ("opening", "bar", "metric", "type") else "center",
                        stretch=key in ("opening", "metric", "type"))
        scroll = ctk.CTkScrollbar(body, command=tree.yview, button_color=theme.CONTROL_BG,
                                  fg_color="transparent")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        tree.tag_configure("weak", foreground=theme.ERROR)
        return tree

    def _sort(self, tree: ttk.Treeview, key: str) -> None:
        """Sorts by a column, toggling direction on repeat clicks. Numbers
        (including '54%' / '2.1') sort numerically."""
        def value(item):
            text = str(tree.set(item, key)).rstrip("%")
            try:
                return (0, float(text))
            except ValueError:
                return (1, text.lower())
        reverse = getattr(tree, "_sort_state", None) == (key, False)
        items = sorted(tree.get_children(), key=value, reverse=reverse)
        for i, item in enumerate(items):
            tree.move(item, "", i)
        tree._sort_state = (key, reverse)

    def refresh(self) -> None:
        db.set_setting(self.conn, "stats_filter", self.filter_var.get())
        s = stats.compute(self.conn, FILTERS[self.filter_var.get()])
        self.summary_label.configure(
            text=f"{s.total_games} games · {s.engine_games} with Stockfish data"
        )
        for tree in (self.openings_tree, self.losses_tree, self.phase_tree, self.clock_tree,
                     self.indicators_tree, self.game_types_tree, self.opening_char_tree,
                     self.recommend_tree):
            tree.delete(*tree.get_children())
        self._refresh_style(FILTERS[self.filter_var.get()], s.openings)
        if self.page_switch.get() == "Repertoire":
            self._refresh_repertoire()

        # openings: most-played first; weak (<40% score) highlighted
        rows = [(k, r) for k, r in s.openings.items() if r.games >= MIN_OPENING_GAMES]
        for (name, color), r in sorted(rows, key=lambda kr: -kr[1].games):
            self.openings_tree.insert("", "end", tags=("weak",) if r.score < 0.4 else (), values=(
                name, color.capitalize(), r.games, f"{r.wins} / {r.draws} / {r.losses}",
                f"{100 * r.score:.0f}%", _bar(r.score),
            ))

        order = ["Bullet", "Blitz", "Rapid", "Daily", "Other"]
        for tc in sorted(s.loss_reasons, key=lambda t: order.index(t) if t in order else 99):
            reasons = s.loss_reasons[tc]
            lost = sum(reasons.values())
            self.losses_tree.insert("", "end", values=(
                tc, lost, _pct(reasons.get("Resigned", 0), lost),
                _pct(reasons.get("Checkmated", 0), lost), _pct(reasons.get("Timeout", 0), lost),
                _pct(reasons.get("Other", 0) + reasons.get("Abandoned", 0), lost),
            ))

        worst_avg = max((p.avg_loss for p in s.phases.values()), default=0) or 1
        for name, p in s.phases.items():
            self.phase_tree.insert("", "end", values=(
                name, p.moves, f"{p.avg_loss / 100:.2f}" if p.moves else "–",
                p.blunders, _bar(p.avg_loss / worst_avg) if p.moves else "",
            ))

        worst_rate = max((c.blunder_rate for c in s.clock.values()), default=0) or 1
        for label, c in s.clock.items():
            self.clock_tree.insert("", "end", values=(
                label, c.moves, f"{100 * c.blunder_rate:.1f}%" if c.moves else "–",
                _bar(c.blunder_rate / worst_rate) if c.moves else "",
            ))
