"""Stats tab: tables that point at your weaknesses — results by opening,
how you lose, accuracy by game phase, and blunders under time pressure.
Recomputed every time the tab is shown (cheap: no engine involved)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import db
import openings_catalog
import stats
import style
import theme

from models import TIME_FILTERS as FILTERS

MIN_OPENING_GAMES = 3
BAR_WIDTH = 12


def _bar(fraction: float) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "–"


class StatsTab(ttk.Frame):
    def __init__(self, master, conn, on_drill=None):
        super().__init__(master, padding=(20, 14))
        self.conn = conn
        # on_drill(CatalogOpening): opens that opening in the Opening Drill tab
        self.on_drill = on_drill
        style = ttk.Style(self)
        style.configure("Treeview", font=theme.FONT_BASE, rowheight=24,
                        background=theme.PANEL_BG, fieldbackground=theme.PANEL_BG)
        style.configure("Treeview.Heading", font=theme.FONT_BOLD)
        style.map("Treeview", background=[("selected", theme.ACCENT)])
        self._build_layout()
        self.bind("<Map>", lambda e: self.refresh())

    def _build_layout(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Your weaknesses", style="Heading.TLabel").pack(side="left")
        self.summary_label = ttk.Label(top, text="", style="Muted.TLabel")
        self.summary_label.pack(side="left", padx=(12, 0), pady=(6, 0))

        self.filter_var = tk.StringVar(value=db.get_setting(self.conn, "stats_filter") or "All games")
        if self.filter_var.get() not in FILTERS:
            self.filter_var.set("All games")
        dropdown = ttk.Combobox(top, textvariable=self.filter_var, values=list(FILTERS),
                                state="readonly", width=20)
        dropdown.pack(side="right")
        dropdown.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(top, text="Games", style="TLabel").pack(side="right", padx=(0, 8))

        pages = ttk.Notebook(self)
        pages.pack(fill="both", expand=True, pady=(10, 0))
        grid = ttk.Frame(pages, padding=(0, 10, 0, 0))
        style_page = ttk.Frame(pages, padding=(0, 6, 0, 0))
        pages.add(grid, text="Overview")
        pages.add(style_page, text="Playing style")
        self._build_style_page(style_page)

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
        self.verdict_label = ttk.Label(page, text="", style="Status.TLabel", wraplength=880, justify="left")
        self.verdict_label.pack(anchor="w")
        self.style_note_label = ttk.Label(page, text="", style="Muted.TLabel", wraplength=880, justify="left")
        self.style_note_label.pack(anchor="w", pady=(2, 0))

        grid = ttk.Frame(page)
        grid.pack(fill="both", expand=True, pady=(8, 0))
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
        ttk.Button(self.recommend_tree.master.master, text="Drill selected opening",
                   style="Secondary.TButton", command=self._drill_selected).pack(anchor="e", pady=(6, 0))

    def _drill_selected(self) -> None:
        selection = self.recommend_tree.selection()
        if selection and self.on_drill:
            self.on_drill(openings_catalog.CATALOG[int(selection[0])])

    def _refresh_style(self, classes, opening_records) -> None:
        r = style.compute_style(self.conn, classes)
        self.verdict_label.config(text=r.verdict)
        note = f"Based on {r.games} Stockfish-analyzed games in this filter."
        if r.multipv_games < r.games:
            note += (f" {r.games - r.multipv_games} were analyzed before top-3 engine moves were "
                     "recorded — Analyze new games re-analyzes them for more accurate labels.")
        self.style_note_label.config(text=note)

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
        frame = ttk.Frame(parent, padding=(0, 0, 12 if col == 0 else 0, 12))
        frame.grid(row=row, column=col, sticky="nsew")
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text=help_text, style="Muted.TLabel", wraplength=440,
                  justify="left").pack(anchor="w", pady=(0, 4))

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=[c[0] for c in columns], show="headings", height=6)
        for key, heading, width in columns:
            tree.heading(key, text=heading, command=lambda k=key, t=tree: self._sort(t, k))
            tree.column(key, width=width,
                        anchor="w" if key in ("opening", "bar", "metric", "type") else "center",
                        stretch=key in ("opening", "metric", "type"))
        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
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
        self.summary_label.config(
            text=f"{s.total_games} games · {s.engine_games} with Stockfish data"
        )
        for tree in (self.openings_tree, self.losses_tree, self.phase_tree, self.clock_tree,
                     self.indicators_tree, self.game_types_tree, self.opening_char_tree,
                     self.recommend_tree):
            tree.delete(*tree.get_children())
        self._refresh_style(FILTERS[self.filter_var.get()], s.openings)

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
