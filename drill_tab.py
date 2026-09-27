"""Opening drill tab: import a PGN repertoire, then get tested on a
randomly-picked full line — named upfront, only that specific line's own
moves count as correct (no silent transposing onto a different prepared
line), wrong moves are retryable, and you get a move recap at the end."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from typing import Optional

import chess

import db
import random

import opening_importer
import opening_notes
from play_window import PlayWindow
import theme
from board_widget import BOARD_PIXELS, ChessBoardWidget
from drill import DrillResult, OpeningDrillSession, format_move_list
from models import PlayerColor


class OpeningDrillTab(ctk.CTkFrame):
    def __init__(self, master, conn):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.session: Optional[OpeningDrillSession] = None

        self._build_layout()
        self._refresh_repertoire_list()

    def _build_layout(self) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        board_pad = ctk.CTkFrame(left, fg_color="transparent")
        board_pad.pack(padx=18, pady=16)

        self.line_name_label = theme.label(board_pad, "", "heading")
        self.line_name_label.pack(anchor="w", pady=(0, 8))

        self.board_widget = ChessBoardWidget(board_pad, self._on_my_move_attempt, bg=theme.PANEL_BG)
        self.board_widget.pack()

        self.status_label = theme.label(board_pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(pady=(12, 0), anchor="w")
        self.move_note_label = theme.label(board_pad, "", "body", wraplength=BOARD_PIXELS,
                                           text_color=theme.ACCENT_HOVER)
        self.move_note_label.pack(pady=(2, 0), anchor="w")
        self.recap_label = theme.label(board_pad, "", "body", wraplength=BOARD_PIXELS)
        self.recap_label.pack(pady=(2, 0), anchor="w")

        button_row = ctk.CTkFrame(board_pad, fg_color="transparent")
        button_row.pack(pady=(10, 0), anchor="w")
        self.retry_button = theme.button(button_row, "Try again", self._on_retry, width=100)
        self.reveal_button = theme.button(button_row, "Show correct move", self._on_reveal, width=160)
        self.play_button = theme.button(button_row, "Play it out vs Stockfish", self._play_it_out,
                                        "primary", width=200)
        self._line_missed = False
        # both hidden until a genuine (non-book) miss is made

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)

        # Setup (pick/import lines) vs Plans (what the opening is about)
        self.right_switch = ctk.CTkSegmentedButton(
            right, values=["Setup", "Plans"], command=self._show_right_view,
            height=34, font=theme.FONT_BOLD, fg_color=theme.PANEL_BG,
            selected_color=theme.CONTROL_BG, selected_hover_color=theme.CONTROL_BG,
            unselected_color=theme.PANEL_BG, unselected_hover_color=theme.PANEL_ALT,
            text_color=theme.TEXT,
        )
        self.right_switch.pack(fill="x", pady=(0, 12))
        setup = ctk.CTkFrame(right, fg_color="transparent")
        plans = theme.card(right)
        self._right_views = {"Setup": setup, "Plans": plans}
        self.plans_body = ctk.CTkScrollableFrame(plans, fg_color="transparent")
        self.plans_body.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- Drill card ----
        drill_card = theme.card(setup)
        drill_card.pack(fill="both", expand=True)
        drill = ctk.CTkFrame(drill_card, fg_color="transparent")
        drill.pack(fill="both", expand=True, padx=18, pady=16)

        theme.label(drill, "Drill", "heading").pack(anchor="w", pady=(0, 8))
        theme.label(drill, "REPERTOIRE", "section").pack(anchor="w", pady=(0, 4))
        self.repertoire_var = tk.StringVar()
        self.repertoire_dropdown = theme.option_menu(
            drill, [""], self.repertoire_var, lambda _: self._refresh_variation_list(), width=300,
        )
        self.repertoire_dropdown.pack(fill="x")

        theme.label(drill, "VARIATION", "section").pack(anchor="w", pady=(12, 4))
        variation_frame = ctk.CTkFrame(drill, fg_color=theme.PANEL_ALT, corner_radius=10)
        variation_frame.pack(fill="both", expand=True)
        self.variation_listbox = theme.listbox(variation_frame, height=6)
        variation_scroll = ctk.CTkScrollbar(variation_frame, command=self.variation_listbox.yview,
                                            button_color=theme.CONTROL_BG, fg_color=theme.PANEL_ALT)
        self.variation_listbox.configure(yscrollcommand=variation_scroll.set)
        self.variation_listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        variation_scroll.pack(side="left", fill="y", pady=8)
        self.variation_listbox.bind("<Double-Button-1>", lambda e: self._start_drill())
        self._variation_roots = []

        self.start_button = theme.button(drill, "Start drill", self._start_drill, "primary")
        self.start_button.pack(fill="x", pady=(12, 6))
        theme.label(
            drill, "Pick a variation (double-click to start), or leave it on Any line "
                   "for a random one. The computer plays the other side.",
            "muted", wraplength=340,
        ).pack(anchor="w", fill="x")

        # ---- Manage card ----
        manage_card = theme.card(setup)
        manage_card.pack(fill="x", pady=(16, 0))
        manage = ctk.CTkFrame(manage_card, fg_color="transparent")
        manage.pack(fill="x", padx=18, pady=16)
        theme.label(manage, "Add or edit lines", "heading").pack(anchor="w", pady=(0, 8))
        for text, command in (("Import PGN file...", self._import_repertoire),
                              ("Paste / type a line...", self._open_paste_dialog),
                              ("Turn lines on/off...", self._open_manage_lines_dialog)):
            theme.button(manage, text, command).pack(fill="x", pady=3)

        self.right_switch.set("Setup")
        self._show_right_view("Setup")
        self.variation_listbox.bind("<<ListboxSelect>>", lambda e: self._render_selected_plans())

    def _show_right_view(self, name: str) -> None:
        for view_name, view in self._right_views.items():
            if view_name == name:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()

    def _render_selected_plans(self) -> None:
        """Plans for the variation highlighted in the list (before drilling)."""
        selection = self._selected_repertoire()
        if selection is None:
            return
        _, root, _ = self._selected_entry()
        self._render_plans(root.line_name if root else None, selection[0], PlayerColor(selection[1]))

    def _render_plans(self, line_name: Optional[str], repertoire_name: str, my_color: PlayerColor) -> None:
        for child in self.plans_body.winfo_children():
            child.destroy()
        body = self.plans_body
        notes = opening_notes.notes_for(line_name, repertoire_name)
        if notes is None:
            theme.label(body, "Plans", "heading").pack(anchor="w", padx=10, pady=(6, 4))
            theme.label(body, "No plan notes for this opening yet. Pick a variation, or start "
                              "a drill, to see its ideas here.", "muted", wraplength=320).pack(
                anchor="w", padx=10)
            return

        def section(title: str) -> None:
            theme.label(body, title, "section").pack(anchor="w", padx=10, pady=(14, 4))

        def bullets(items) -> None:
            for item in items:
                theme.label(body, f"•  {item}", wraplength=320).pack(anchor="w", padx=10, pady=1)

        theme.label(body, notes.title, "heading", wraplength=330).pack(anchor="w", padx=10, pady=(6, 4))
        theme.label(body, notes.idea, wraplength=330).pack(anchor="w", padx=10)
        # your side's plans first
        sides = [("WHITE", notes.white), ("BLACK", notes.black)]
        if my_color == PlayerColor.BLACK:
            sides.reverse()
        for i, (side, items) in enumerate(sides):
            section(f"YOUR PLANS ({side})" if i == 0 else f"OPPONENT'S PLANS ({side})")
            bullets(items)
        section("PAWN STRUCTURE")
        theme.label(body, notes.structure, wraplength=320).pack(anchor="w", padx=10)
        section("WATCH OUT FOR")
        bullets(notes.watch_out)

    def _show_move_note(self, san: Optional[str]) -> None:
        """The imported PGN's comment on the move just played, if any."""
        note = self.session.last_move_note() if self.session else None
        # moves without a comment leave the previous note up (it's prefixed
        # with its move), so a quick opponent reply doesn't wipe your move's note
        if note:
            self.move_note_label.configure(text=f"{san}: {note}" if san else note)

    # ---------- Repertoire management ----------

    def _refresh_repertoire_list(self) -> None:
        pairs = db.list_repertoires(self.conn)
        self._repertoire_labels = [f"{name} ({color})" for name, color in pairs]
        self._repertoire_pairs = pairs
        self.repertoire_dropdown.configure(values=self._repertoire_labels or [""])
        if self.repertoire_var.get() not in self._repertoire_labels:
            self.repertoire_var.set(self._repertoire_labels[0] if self._repertoire_labels else "")
        self._refresh_variation_list()
        if self.session is None:
            if self._repertoire_labels:
                self.status_label.configure(text="Pick a repertoire and click Start drill.",
                                            text_color=theme.TEXT_MUTED)
            else:
                self.status_label.configure(
                    text="No repertoires yet. Import a PGN file or paste a line to begin.",
                    text_color=theme.TEXT_MUTED,
                )

    def _dialog(self, parent, title: str, geometry: Optional[str] = None) -> ctk.CTkToplevel:
        dialog = ctk.CTkToplevel(parent)
        dialog.title(title)
        dialog.configure(fg_color=theme.BG)
        if geometry:
            dialog.geometry(geometry)
        dialog.transient(parent.winfo_toplevel())
        dialog.after(50, dialog.lift)   # CTkToplevel can open behind its parent on Windows
        return dialog

    def _ask_repertoire_name_and_color(self, parent=None):
        """One small modal dialog for both questions — a name field plus
        White/Black radio buttons. Returns (name, PlayerColor) or None if
        cancelled. Offers existing repertoire names so lines can be added
        to one you already have."""
        parent = parent or self
        dialog = self._dialog(parent, "Save to repertoire")
        dialog.resizable(False, False)

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=18)

        theme.label(body, "Repertoire name").pack(anchor="w")
        theme.label(body, "Pick an existing one to add to it, or type a new name.",
                    "muted").pack(anchor="w", pady=(0, 6))
        name_var = tk.StringVar()
        existing_names = sorted({name for name, _ in db.list_repertoires(self.conn)})
        name_box = ctk.CTkComboBox(
            body, variable=name_var, values=existing_names or [""], width=320, height=34,
            fg_color=theme.INPUT_BG, border_color=theme.BORDER_STRONG, button_color=theme.CONTROL_BG,
            text_color=theme.TEXT, dropdown_fg_color=theme.PANEL_ALT, font=theme.FONT_BASE,
        )
        name_var.set("")
        name_box.pack(fill="x")

        theme.label(body, "I play").pack(anchor="w", pady=(14, 4))
        color_var = tk.StringVar(value=PlayerColor.WHITE.value)
        color_row = ctk.CTkFrame(body, fg_color="transparent")
        color_row.pack(anchor="w")
        for text, value in (("White", PlayerColor.WHITE.value), ("Black", PlayerColor.BLACK.value)):
            ctk.CTkRadioButton(color_row, text=text, value=value, variable=color_var,
                               fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                               text_color=theme.TEXT, font=theme.FONT_BASE).pack(side="left", padx=(0, 20))

        result = {}

        def on_ok(event=None):
            name = name_var.get().strip()
            if not name:
                name_box.focus_set()
                return
            result["value"] = (name, PlayerColor(color_var.get()))
            dialog.destroy()

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(18, 0))
        theme.button(buttons, "Cancel", dialog.destroy, width=90).pack(side="right")
        theme.button(buttons, "Save", on_ok, "primary", width=90).pack(side="right", padx=(0, 8))

        dialog.bind("<Return>", on_ok)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        name_box.focus_set()
        dialog.after(100, dialog.grab_set)   # grab needs the window mapped first
        dialog.wait_window()
        return result.get("value")

    def _import_repertoire(self) -> None:
        path = filedialog.askopenfilename(
            title="Select PGN repertoire file", filetypes=[("PGN files", "*.pgn"), ("All files", "*.*")]
        )
        if not path:
            return
        name_and_color = self._ask_repertoire_name_and_color()
        if name_and_color is None:
            return
        name, my_color = name_and_color

        try:
            count = opening_importer.import_pgn_file(path, name, my_color, self.conn)
            if count:
                messagebox.showinfo("Import complete", f"Imported {count} positions into '{name}'.")
            else:
                messagebox.showinfo("Already imported",
                                    f"These lines are already in '{name}' — their move notes were updated.")
            self._refresh_repertoire_list()
            self._select_repertoire(name, my_color)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    def _open_paste_dialog(self) -> None:
        dialog = self._dialog(self, "Paste an opening line", "520x320")
        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=18)

        theme.label(
            body,
            "Type or paste moves in standard notation, e.g.:\n"
            "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6\n"
            "Sidelines in parentheses become branches, e.g. 2. Nf3 (2. Nc3 Nc6)",
            wraplength=470,
        ).pack(anchor="w", pady=(0, 8))

        text_box = ctk.CTkTextbox(body, height=120, wrap="word", fg_color=theme.INPUT_BG,
                                  border_color=theme.BORDER_STRONG, border_width=1,
                                  text_color=theme.TEXT, font=theme.FONT_BASE, corner_radius=8)
        text_box.pack(fill="both", expand=True)

        def do_import():
            moves_text = text_box.get("1.0", "end").strip()
            if not moves_text:
                return
            name_and_color = self._ask_repertoire_name_and_color(parent=dialog)
            if name_and_color is None:
                return
            name, my_color = name_and_color

            pgn_text = f'[Event "{name}"]\n\n{moves_text} *\n'
            try:
                count = opening_importer.import_pgn_text(pgn_text, name, my_color, self.conn)
                if count == 0:
                    messagebox.showwarning(
                        "Nothing imported",
                        "No new moves were imported — check the notation, or this line "
                        "may already be in that repertoire.",
                    )
                    return
                messagebox.showinfo("Import complete", f"Imported {count} positions into '{name}'.",
                                    parent=dialog)
                self._refresh_repertoire_list()
                self._select_repertoire(name, my_color)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Import failed", str(e))

        theme.button(body, "Import this line", do_import, "primary").pack(pady=(12, 0), anchor="e")

    def _open_manage_lines_dialog(self) -> None:
        """Lets the user turn individual lines on/off without deleting
        them — disabled lines are excluded from random selection and
        book-move matching during drilling, but stay in the database."""
        repertoires = db.list_repertoires(self.conn)
        if not repertoires:
            messagebox.showinfo("No lines yet", "Import a repertoire first.")
            return

        dialog = self._dialog(self, "Turn lines on/off", "480x580")
        theme.label(dialog, "Uncheck a line to exclude it from drilling — it stays saved.",
                    "muted", wraplength=440).pack(padx=20, pady=(18, 8), anchor="w")

        scroll_frame = ctk.CTkScrollableFrame(dialog, fg_color=theme.PANEL_BG, corner_radius=12)
        scroll_frame.pack(fill="both", expand=True, padx=20)

        # keep references so checkbox callbacks and garbage collection behave
        self._manage_line_vars = []

        for repertoire_name, my_color in repertoires:
            theme.label(scroll_frame, f"{repertoire_name} ({my_color})", "heading").pack(
                anchor="w", padx=8, pady=(12, 4))

            roots = db.all_root_lines(self.conn, repertoire_name, my_color)
            for root in roots:
                var = tk.BooleanVar(value=root.is_enabled)
                self._manage_line_vars.append(var)

                def on_toggle(root_id=root.id, var=var):
                    db.set_line_enabled(self.conn, root_id, var.get())

                ctk.CTkCheckBox(
                    scroll_frame, text=root.line_name or root.move_san, variable=var,
                    command=on_toggle, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                    checkmark_color=theme.ACCENT_TEXT, text_color=theme.TEXT, font=theme.FONT_BASE,
                ).pack(anchor="w", padx=12, pady=3)

        def on_close():
            dialog.destroy()
            self._refresh_repertoire_list()

        theme.button(dialog, "Done", on_close, "primary", width=100).pack(pady=14)
        dialog.protocol("WM_DELETE_WINDOW", on_close)

    # ---------- Drill flow ----------

    def _refresh_variation_list(self) -> None:
        """Lists the enabled lines of the selected repertoire, with 'Any
        line' first for a random pick."""
        self.variation_listbox.delete(0, tk.END)
        selection = self._selected_repertoire()
        roots = db.root_lines(self.conn, *selection) if selection else []
        roots.sort(key=lambda r: (r.line_name or r.move_san).lower())
        self._variation_roots = roots
        weak = db.weak_item_ids(self.conn, "line")
        weak_count = sum(1 for r in roots if r.id in weak)
        # (kind, root, label): "random" / "weak" (lines you keep missing) / "line"
        self._variation_entries = [("random", None, "Any line (random)")]
        if weak_count:
            self._variation_entries.append(("weak", None, f"Weak lines ({weak_count} to fix)"))
        self._variation_entries += [("line", r, r.line_name or r.move_san) for r in roots]
        for _, _, label in self._variation_entries:
            self.variation_listbox.insert(tk.END, label)
        self.variation_listbox.selection_set(0)

    def _selected_entry(self):
        sel = self.variation_listbox.curselection()
        entries = getattr(self, "_variation_entries", [])
        if not sel or sel[0] >= len(entries):
            return ("random", None, "")
        return entries[sel[0]]

    def _selected_root_id(self) -> Optional[str]:
        kind, root, _ = self._selected_entry()
        if kind == "line":
            return root.id
        if kind == "weak":
            # weighted pick: lines you've missed more come up more often
            weak = db.weak_item_ids(self.conn, "line")
            candidates = [r for r in self._variation_roots if r.id in weak]
            if candidates:
                return random.choices(candidates, weights=[weak[r.id] for r in candidates])[0].id
        return None

    def _select_repertoire(self, name: str, my_color: PlayerColor) -> None:
        """Points the dropdown at a just-imported repertoire so Start drill
        uses it right away."""
        pair = (name, my_color.value)
        if pair in self._repertoire_pairs:
            self.repertoire_var.set(self._repertoire_labels[self._repertoire_pairs.index(pair)])
            self._refresh_variation_list()

    SUGGESTIONS_REPERTOIRE = "Style suggestions"

    def drill_suggestion(self, line_name: str, my_color: PlayerColor, moves: str) -> None:
        """Called from the Stats tab's opening recommendations: imports the
        line into the 'Style suggestions' repertoire (once), then selects
        and starts drilling it."""
        rep = self.SUGGESTIONS_REPERTOIRE
        root = next((r for r in db.all_root_lines(self.conn, rep, my_color.value)
                     if r.line_name == line_name), None)
        if root is None:
            opening_importer.import_pgn_text(f'[Event "{line_name}"]\n\n{moves} *\n', rep, my_color, self.conn)
        elif not root.is_enabled:
            db.set_line_enabled(self.conn, root.id, True)

        self._refresh_repertoire_list()
        self._select_repertoire(rep, my_color)
        names = [root.line_name if root else None for _, root, _ in self._variation_entries]
        if line_name in names:
            self.variation_listbox.selection_clear(0, tk.END)
            self.variation_listbox.selection_set(names.index(line_name))
            self.variation_listbox.see(names.index(line_name))
        self._start_drill()

    def _selected_repertoire(self):
        label = self.repertoire_var.get()
        if label not in getattr(self, "_repertoire_labels", []):
            return None
        return self._repertoire_pairs[self._repertoire_labels.index(label)]

    def _start_drill(self) -> None:
        selection = self._selected_repertoire()
        if selection is None:
            messagebox.showinfo("No repertoire", "Import and select a repertoire first.")
            return
        name, color_str = selection
        my_color = PlayerColor(color_str)

        kind, _, _ = self._selected_entry()
        self.session = OpeningDrillSession(self.conn, name, my_color)
        if not self.session.start(self._selected_root_id()):
            messagebox.showinfo("Empty repertoire", "That repertoire has no lines in it yet.")
            return

        self._line_missed = False
        self.play_button.pack_forget()
        self.recap_label.configure(text="")
        self.move_note_label.configure(text="")
        self._render_plans(self.session.line_name, name, my_color)
        self.right_switch.set("Plans")
        self._show_right_view("Plans")
        self._hide_retry_buttons()
        self.start_button.configure(
            text={"random": "New random line", "weak": "Next weak line"}.get(kind, "Drill again")
        )
        self.line_name_label.configure(text=f"Drilling: {self.session.line_name}")
        # show the board from your side when drilling Black
        self.board_widget.show_board(self.session.board, flipped=my_color == PlayerColor.BLACK)
        self._advance()

    def _advance(self) -> None:
        """Plays opponent moves automatically until it's your turn or the
        line is complete."""
        if self.session is None:
            return

        if self.session.is_finished():
            self._show_completion()
            return

        if self.session.is_my_turn():
            self.board_widget.show_board(self.session.board)
            self.status_label.configure(text="Your move.", text_color=theme.TEXT)
            return

        san = self.session.play_opponent_move()
        self.board_widget.show_board(self.session.board)
        self.status_label.configure(text=f"Opponent played {san}. Your move.", text_color=theme.TEXT)
        self._show_move_note(san)
        self.after(300, self._advance)

    def _show_completion(self) -> None:
        recap = format_move_list(self.session.full_line_san())
        self.status_label.configure(text="End of line — nice work. Click New random line for another.",
                                 text_color=theme.SUCCESS)
        self.recap_label.configure(text=f"{self.session.line_name}:\n{recap}")
        self._hide_retry_buttons()
        # one attempt per line: clean run = correct (clears it from Weak lines over time)
        db.record_attempt(self.conn, "line", self.session.target_path[0].id, not self._line_missed)
        self.play_button.pack(side="left")

    def _play_it_out(self) -> None:
        """Continue the finished line as a real game against Stockfish."""
        if self.session is None:
            return
        color = chess.WHITE if self.session.my_color == PlayerColor.WHITE else chess.BLACK
        PlayWindow(self, self.session.board.fen(), title=f"Play it out — {self.session.line_name}",
                   my_color=color)

    def _on_my_move_attempt(self, move: chess.Move) -> None:
        if self.session is None or not self.session.is_my_turn():
            return
        result: DrillResult = self.session.attempt_my_move(move)
        self.board_widget.show_board(self.session.board)

        if result.correct:
            self._hide_retry_buttons()
            self.status_label.configure(text=f"Correct — {result.played_san}.", text_color=theme.SUCCESS)
            self._show_move_note(result.played_san)
            self.after(700, self._advance)
        else:
            # nothing was pushed — board is unchanged, so retry just works
            self._line_missed = True
            played_note = f"you played {result.played_san}" if result.played_san else "that's not the move here"
            if result.in_book:
                self.status_label.configure(
                    text=f"Not this line — {played_note}. That's book theory in a "
                         f"different line, but not the one you're drilling.",
                    text_color=theme.ERROR,
                )
            else:
                self.status_label.configure(
                    text=f"Not correct — {played_note}.",
                    text_color=theme.ERROR,
                )
            self._show_retry_buttons()

    def _show_retry_buttons(self) -> None:
        self.retry_button.pack(side="left")
        self.reveal_button.pack(side="left", padx=(8, 0))

    def _hide_retry_buttons(self) -> None:
        self.retry_button.pack_forget()
        self.reveal_button.pack_forget()

    def _on_retry(self) -> None:
        self._hide_retry_buttons()
        self.status_label.configure(text="Your move — try again.", text_color=theme.TEXT)

    def _on_reveal(self) -> None:
        if self.session is None:
            return
        self._line_missed = True
        san = self.session.reveal_and_advance()
        self.board_widget.show_board(self.session.board)
        self._hide_retry_buttons()
        if san is None:
            # shouldn't normally happen (reveal is only offered mid-line),
            # but avoid showing a broken "Correct move was None" message
            self.status_label.configure(text="Nothing left to reveal.", text_color=theme.TEXT)
            self._advance()
            return
        self.status_label.configure(text=f"Correct move was {san}.", text_color=theme.TEXT)
        self._show_move_note(san)
        self.after(700, self._advance)
