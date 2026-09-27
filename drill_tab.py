"""Opening drill tab: import a PGN repertoire, then get tested on a
randomly-picked full line — named upfront, only that specific line's own
moves count as correct (no silent transposing onto a different prepared
line), wrong moves are retryable, and you get a move recap at the end."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import chess

import db
import opening_importer
import theme
from board_widget import BOARD_PIXELS, ChessBoardWidget
from drill import DrillResult, OpeningDrillSession, format_move_list
from models import PlayerColor


class OpeningDrillTab(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master)
        self.conn = conn
        self.session: Optional[OpeningDrillSession] = None

        self._build_layout()
        self._refresh_repertoire_list()

    def _build_layout(self) -> None:
        left = theme.panel(self)
        left.master.pack(side="left", padx=(20, 10), pady=14, anchor="n")
        board_pad = ttk.Frame(left, style="Panel.TFrame", padding=16)
        board_pad.pack()

        self.line_name_label = ttk.Label(board_pad, text="", style="PanelHeading.TLabel")
        self.line_name_label.pack(anchor="w", pady=(0, 8))

        self.board_widget = ChessBoardWidget(board_pad, self._on_my_move_attempt)
        self.board_widget.pack()

        self.status_label = ttk.Label(
            board_pad, text="", style="PanelStatus.TLabel",
            wraplength=BOARD_PIXELS, justify="left",
        )
        self.status_label.pack(pady=(12, 0), anchor="w")

        self.recap_label = ttk.Label(
            board_pad, text="", style="Panel.TLabel", wraplength=BOARD_PIXELS, justify="left",
        )
        self.recap_label.pack(pady=(6, 0), anchor="w")

        button_row = ttk.Frame(board_pad, style="Panel.TFrame")
        button_row.pack(pady=(10, 0), anchor="w")
        self.retry_button = ttk.Button(button_row, text="Try again", style="Secondary.TButton",
                                        command=self._on_retry)
        self.reveal_button = ttk.Button(button_row, text="Show correct move", style="Secondary.TButton",
                                         command=self._on_reveal)
        # both hidden until a genuine (non-book) miss is made

        right = ttk.Frame(self, padding=(10, 14, 20, 14))
        right.pack(side="left", fill="both", expand=True)

        # ---- Drill ----
        ttk.Label(right, text="Drill", style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(right, text="Repertoire", style="TLabel").pack(anchor="w", pady=(0, 4))
        self.repertoire_var = tk.StringVar()
        self.repertoire_dropdown = ttk.Combobox(
            right, textvariable=self.repertoire_var, state="readonly", width=28
        )
        self.repertoire_dropdown.pack(fill="x")
        self.repertoire_dropdown.bind("<<ComboboxSelected>>", lambda e: self._refresh_variation_list())

        ttk.Label(right, text="Variation", style="TLabel").pack(anchor="w", pady=(10, 4))
        variation_frame = ttk.Frame(right)
        variation_frame.pack(fill="x")
        variation_scroll = ttk.Scrollbar(variation_frame, orient="vertical")
        self.variation_listbox = tk.Listbox(
            variation_frame, height=10, activestyle="none", font=theme.FONT_BASE,
            yscrollcommand=variation_scroll.set, exportselection=False,
            selectbackground=theme.ACCENT, selectforeground=theme.ACCENT_TEXT,
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        variation_scroll.config(command=self.variation_listbox.yview)
        self.variation_listbox.pack(side="left", fill="x", expand=True)
        variation_scroll.pack(side="left", fill="y")
        self.variation_listbox.bind("<Double-Button-1>", lambda e: self._start_drill())
        self._variation_roots = []

        self.start_button = ttk.Button(right, text="Start drill", style="Accent.TButton",
                                       command=self._start_drill)
        self.start_button.pack(fill="x", pady=(10, 4))
        ttk.Label(
            right, text="Pick a variation (double-click to start), or leave it on "
                        "Any line for a random one. The computer plays the other side.",
            style="Muted.TLabel", wraplength=300, justify="left",
        ).pack(anchor="w")

        # ---- Manage repertoires ----
        ttk.Label(right, text="Add or edit lines", style="Section.TLabel").pack(anchor="w", pady=(22, 6))
        ttk.Button(right, text="Import PGN file...", style="Secondary.TButton",
                   command=self._import_repertoire).pack(fill="x", pady=4)
        ttk.Button(right, text="Paste / type a line...", style="Secondary.TButton",
                   command=self._open_paste_dialog).pack(fill="x", pady=4)
        ttk.Button(right, text="Turn lines on/off...", style="Secondary.TButton",
                   command=self._open_manage_lines_dialog).pack(fill="x", pady=4)

    # ---------- Repertoire management ----------

    def _refresh_repertoire_list(self) -> None:
        pairs = db.list_repertoires(self.conn)
        labels = [f"{name} ({color})" for name, color in pairs]
        self._repertoire_pairs = pairs
        self.repertoire_dropdown["values"] = labels
        if labels and not self.repertoire_var.get():
            self.repertoire_dropdown.current(0)
        self._refresh_variation_list()
        if self.session is None:
            if labels:
                self.status_label.config(text="Pick a repertoire and click Start drill.",
                                         foreground=theme.TEXT_MUTED)
            else:
                self.status_label.config(
                    text="No repertoires yet. Import a PGN file or paste a line to begin.",
                    foreground=theme.TEXT_MUTED,
                )

    def _ask_repertoire_name_and_color(self, parent=None):
        """One small modal dialog for both questions — a name field plus
        White/Black radio buttons. Returns (name, PlayerColor) or None if
        cancelled. Offers existing repertoire names so lines can be added
        to one you already have."""
        parent = parent or self
        dialog = tk.Toplevel(parent)
        dialog.title("Save to repertoire")
        dialog.configure(bg=theme.BG)
        dialog.resizable(False, False)
        dialog.transient(parent.winfo_toplevel())

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Repertoire name", style="TLabel").pack(anchor="w")
        ttk.Label(body, text="Pick an existing one to add to it, or type a new name.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        name_var = tk.StringVar()
        existing_names = sorted({name for name, _ in db.list_repertoires(self.conn)})
        name_box = ttk.Combobox(body, textvariable=name_var, values=existing_names, width=34)
        name_box.pack(fill="x")

        ttk.Label(body, text="I play", style="TLabel").pack(anchor="w", pady=(12, 2))
        color_var = tk.StringVar(value=PlayerColor.WHITE.value)
        color_row = ttk.Frame(body)
        color_row.pack(anchor="w")
        ttk.Radiobutton(color_row, text="White", value=PlayerColor.WHITE.value,
                        variable=color_var).pack(side="left")
        ttk.Radiobutton(color_row, text="Black", value=PlayerColor.BLACK.value,
                        variable=color_var).pack(side="left", padx=(16, 0))

        result = {}

        def on_ok(event=None):
            name = name_var.get().strip()
            if not name:
                name_box.focus_set()
                return
            result["value"] = (name, PlayerColor(color_var.get()))
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", style="Secondary.TButton",
                   command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Accent.TButton",
                   command=on_ok).pack(side="right", padx=(0, 8))

        dialog.bind("<Return>", on_ok)
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        name_box.focus_set()
        dialog.grab_set()
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
            messagebox.showinfo("Import complete", f"Imported {count} positions into '{name}'.")
            self._refresh_repertoire_list()
            self._select_repertoire(name, my_color)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    def _open_paste_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Paste an opening line")
        dialog.geometry("480x280")
        dialog.configure(bg=theme.BG)

        ttk.Label(
            dialog,
            text="Type or paste moves in standard notation, e.g.:\n"
                 "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6\n"
                 "Sidelines in parentheses become branches, e.g. 2. Nf3 (2. Nc3 Nc6)",
            justify="left", wraplength=460, style="TLabel",
        ).pack(padx=14, pady=(14, 6), anchor="w")

        text_box = tk.Text(dialog, height=6, wrap="word", relief="solid",
                            borderwidth=1, highlightthickness=0, font=theme.FONT_BASE)
        text_box.pack(fill="both", expand=True, padx=14, pady=4)

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
                        "No moves were recognized — check the notation and try again.",
                    )
                    return
                messagebox.showinfo("Import complete", f"Imported {count} positions into '{name}'.",
                                    parent=dialog)
                self._refresh_repertoire_list()
                self._select_repertoire(name, my_color)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Import failed", str(e))

        ttk.Button(dialog, text="Import this line", style="Accent.TButton",
                   command=do_import).pack(pady=10)

    def _open_manage_lines_dialog(self) -> None:
        """Lets the user turn individual lines on/off without deleting
        them — disabled lines are excluded from random selection and
        book-move matching during drilling, but stay in the database."""
        repertoires = db.list_repertoires(self.conn)
        if not repertoires:
            messagebox.showinfo("No lines yet", "Import a repertoire first.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Manage lines")
        dialog.geometry("460x560")
        dialog.configure(bg=theme.BG)

        ttk.Label(
            dialog, text="Uncheck a line to exclude it from drilling — it stays saved.",
            style="Muted.TLabel", wraplength=430,
        ).pack(padx=14, pady=(14, 6), anchor="w")

        canvas = tk.Canvas(dialog, bg=theme.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=(0, 14))
        scrollbar.pack(side="left", fill="y", pady=(0, 14))

        # keep references so checkbox callbacks and garbage collection behave
        self._manage_line_vars = []

        for repertoire_name, my_color in repertoires:
            ttk.Label(
                scroll_frame, text=f"{repertoire_name} ({my_color})", style="Heading.TLabel",
            ).pack(anchor="w", pady=(12, 4))

            roots = db.all_root_lines(self.conn, repertoire_name, my_color)
            for root in roots:
                var = tk.BooleanVar(value=root.is_enabled)
                self._manage_line_vars.append(var)

                def on_toggle(root_id=root.id, var=var):
                    db.set_line_enabled(self.conn, root_id, var.get())

                cb = tk.Checkbutton(
                    scroll_frame, text=root.line_name or root.move_san, variable=var,
                    command=on_toggle, bg=theme.BG, activebackground=theme.BG,
                    anchor="w", font=theme.FONT_BASE,
                )
                cb.pack(anchor="w", fill="x")

        def on_close():
            dialog.destroy()
            self._refresh_repertoire_list()

        ttk.Button(dialog, text="Done", style="Accent.TButton", command=on_close).pack(pady=10)

    # ---------- Drill flow ----------

    def _refresh_variation_list(self) -> None:
        """Lists the enabled lines of the selected repertoire, with 'Any
        line' first for a random pick."""
        self.variation_listbox.delete(0, tk.END)
        selection = self._selected_repertoire()
        roots = db.root_lines(self.conn, *selection) if selection else []
        roots.sort(key=lambda r: (r.line_name or r.move_san).lower())
        self._variation_roots = roots
        self.variation_listbox.insert(tk.END, "Any line (random)")
        for r in roots:
            self.variation_listbox.insert(tk.END, r.line_name or r.move_san)
        self.variation_listbox.selection_set(0)

    def _selected_root_id(self) -> Optional[str]:
        sel = self.variation_listbox.curselection()
        if not sel or sel[0] == 0:
            return None
        return self._variation_roots[sel[0] - 1].id

    def _select_repertoire(self, name: str, my_color: PlayerColor) -> None:
        """Points the dropdown at a just-imported repertoire so Start drill
        uses it right away."""
        pair = (name, my_color.value)
        if pair in self._repertoire_pairs:
            self.repertoire_dropdown.current(self._repertoire_pairs.index(pair))
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
        names = [r.line_name for r in self._variation_roots]
        if line_name in names:
            self.variation_listbox.selection_clear(0, tk.END)
            self.variation_listbox.selection_set(names.index(line_name) + 1)
            self.variation_listbox.see(names.index(line_name) + 1)
        self._start_drill()

    def _selected_repertoire(self):
        idx = self.repertoire_dropdown.current()
        if idx < 0 or idx >= len(self._repertoire_pairs):
            return None
        return self._repertoire_pairs[idx]

    def _start_drill(self) -> None:
        selection = self._selected_repertoire()
        if selection is None:
            messagebox.showinfo("No repertoire", "Import and select a repertoire first.")
            return
        name, color_str = selection
        my_color = PlayerColor(color_str)

        self.session = OpeningDrillSession(self.conn, name, my_color)
        if not self.session.start(self._selected_root_id()):
            messagebox.showinfo("Empty repertoire", "That repertoire has no lines in it yet.")
            return

        self.recap_label.config(text="")
        self._hide_retry_buttons()
        self.start_button.config(
            text="New random line" if self._selected_root_id() is None else "Drill again"
        )
        self.line_name_label.config(text=f"Drilling: {self.session.line_name}")
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
            self.status_label.config(text="Your move.", foreground=theme.TEXT)
            return

        san = self.session.play_opponent_move()
        self.board_widget.show_board(self.session.board)
        self.status_label.config(text=f"Opponent played {san}. Your move.", foreground=theme.TEXT)
        self.after(300, self._advance)

    def _show_completion(self) -> None:
        recap = format_move_list(self.session.full_line_san())
        self.status_label.config(text="End of line — nice work. Click New random line for another.",
                                 foreground=theme.SUCCESS)
        self.recap_label.config(text=f"{self.session.line_name}:\n{recap}")
        self._hide_retry_buttons()

    def _on_my_move_attempt(self, move: chess.Move) -> None:
        if self.session is None or not self.session.is_my_turn():
            return
        result: DrillResult = self.session.attempt_my_move(move)
        self.board_widget.show_board(self.session.board)

        if result.correct:
            self._hide_retry_buttons()
            self.status_label.config(text=f"Correct — {result.played_san}.", foreground=theme.SUCCESS)
            self.after(700, self._advance)
        else:
            # nothing was pushed — board is unchanged, so retry just works
            played_note = f"you played {result.played_san}" if result.played_san else "that's not the move here"
            if result.in_book:
                self.status_label.config(
                    text=f"Not this line — {played_note}. That's book theory in a "
                         f"different line, but not the one you're drilling.",
                    foreground=theme.ERROR,
                )
            else:
                self.status_label.config(
                    text=f"Not correct — {played_note}.",
                    foreground=theme.ERROR,
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
        self.status_label.config(text="Your move — try again.", foreground=theme.TEXT)

    def _on_reveal(self) -> None:
        if self.session is None:
            return
        san = self.session.reveal_and_advance()
        self.board_widget.show_board(self.session.board)
        self._hide_retry_buttons()
        if san is None:
            # shouldn't normally happen (reveal is only offered mid-line),
            # but avoid showing a broken "Correct move was None" message
            self.status_label.config(text="Nothing left to reveal.", foreground=theme.TEXT)
            self._advance()
            return
        self.status_label.config(text=f"Correct move was {san}.", foreground=theme.TEXT)
        self.after(700, self._advance)
