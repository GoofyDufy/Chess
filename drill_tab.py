"""Opening drill tab: import a PGN repertoire, then get tested on a
randomly-picked full line — named upfront, only that specific line's own
moves count as correct (no silent transposing onto a different prepared
line), wrong moves are retryable, and you get a move recap at the end."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from typing import List, Optional

import chess

import db
import explorer
import lichess_ui
import random

import opening_importer
import opening_tree
import opening_notes
import progression
from play_window import PlayWindow
import theme
from board_widget import BOARD_PIXELS, ChessBoardWidget
from drill import DrillResult, OpeningDrillSession, format_move_list
from models import PlayerColor
from tree_view import TreeView, move_label as tree_view_label, short_name

ADD_LINES = "Add lines"
TREE = "Tree"


class OpeningDrillTab(ctk.CTkFrame):
    def __init__(self, master, conn):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.session: Optional[OpeningDrillSession] = None

        self._build_layout()
        self._refresh_repertoire_list()
        top = self.winfo_toplevel()
        top.bind("<Left>", lambda e: self._on_arrow_key(-1), add="+")
        top.bind("<Right>", lambda e: self._on_arrow_key(+1), add="+")
        if not self._repertoire_labels:
            # nothing to drill yet — open on the import buttons
            self.right_switch.set(ADD_LINES)
            self._show_right_view(ADD_LINES)

    def _build_layout(self) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        board_pad = ctk.CTkFrame(left, fg_color="transparent")
        board_pad.pack(padx=18, pady=16)

        header = ctk.CTkFrame(board_pad, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        # step through the full line on the board (shown with Show full line)
        self.step_next_button = theme.button(header, "▶", lambda: self._step_preview(+1), width=36, height=28)
        self.step_prev_button = theme.button(header, "◀", lambda: self._step_preview(-1), width=36, height=28)
        self.line_name_label = theme.label(header, "", "heading", wraplength=BOARD_PIXELS - 90)
        self.line_name_label.pack(side="left", fill="x")
        self._preview_index: Optional[int] = None   # None = the board follows the drill
        self._awaiting_retry = False                 # a miss is waiting for Try again / Show correct move
        self._offer_restart = False                  # after Show correct move: Same line / New random line

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
        self.line_toggle = theme.button(button_row, "Show full line", self._toggle_full_line, width=116)
        self._show_full_line = False
        self.retry_button = theme.button(button_row, "Try again", self._on_retry, width=100)
        self.reveal_button = theme.button(button_row, "Show correct move", self._on_reveal, width=160)
        # end-of-line choices (shown once a line is complete)
        # widths: toggle + these two fit the board's width (352 px on a 768-px-tall screen)
        self.same_line_button = theme.button(button_row, "Same line", self._drill_same_line, width=96)
        self.new_line_button = theme.button(button_row, "New random line", self._drill_new_random_line,
                                            "primary", width=124)
        self.play_button = theme.button(button_row, "Play it out", self._play_it_out, width=100)
        self._line_missed = False
        # both hidden until a genuine (non-book) miss is made

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)

        # Setup (pick a line) vs Plans (what the opening is about) vs Add lines
        # (import/edit) — separate views so the column fits short screens
        self.right_switch = ctk.CTkSegmentedButton(
            right, values=["Setup", "Plans", TREE, ADD_LINES], command=self._show_right_view,
            height=34, font=theme.FONT_BOLD, fg_color=theme.PANEL_BG,
            selected_color=theme.CONTROL_BG, selected_hover_color=theme.CONTROL_BG,
            unselected_color=theme.PANEL_BG, unselected_hover_color=theme.PANEL_ALT,
            text_color=theme.TEXT,
        )
        self.right_switch.pack(fill="x", pady=(0, 12))
        setup = ctk.CTkFrame(right, fg_color="transparent")
        plans = theme.card(right)
        add_lines = ctk.CTkFrame(right, fg_color="transparent")
        tree = ctk.CTkFrame(right, fg_color="transparent")
        self._right_views = {"Setup": setup, "Plans": plans, TREE: tree, ADD_LINES: add_lines}
        self._build_tree_view(tree)
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
        self.preview_label = theme.label(drill, "", "muted", wraplength=340)
        self.preview_label.pack(anchor="w", fill="x", pady=(6, 0))
        self._variation_roots = []

        self.start_button = theme.button(drill, "Start drill", self._start_drill, "primary")
        self.start_button.pack(fill="x", pady=(12, 6))
        theme.label(
            drill, "Pick a variation (double-click to start), or leave it on Any line "
                   "for a random one. The computer plays the other side.",
            "muted", wraplength=340,
        ).pack(anchor="w", fill="x")

        # ---- Manage card ----
        manage_card = theme.card(add_lines)
        manage_card.pack(fill="x")
        manage = ctk.CTkFrame(manage_card, fg_color="transparent")
        manage.pack(fill="x", padx=18, pady=16)
        theme.label(manage, "Add or edit lines", "heading").pack(anchor="w", pady=(0, 8))
        for text, command in (("Import PGN file...", self._import_repertoire),
                              ("Paste / type a line...", self._open_paste_dialog),
                              ("Turn lines on/off...", self._open_manage_lines_dialog)):
            theme.button(manage, text, command).pack(fill="x", pady=3)

        # ---- Lichess card: real-world opponent replies ----
        lichess_card = theme.card(add_lines)
        lichess_card.pack(fill="x", pady=(16, 0))
        lichess = ctk.CTkFrame(lichess_card, fg_color="transparent")
        lichess.pack(fill="x", padx=18, pady=16)
        theme.label(lichess, "Real opponents (Lichess)", "heading").pack(anchor="w", pady=(0, 4))
        self.lichess_label = theme.label(lichess, "", "muted", wraplength=340)
        self.lichess_label.pack(anchor="w", fill="x", pady=(0, 8))
        self.lichess_button = theme.button(lichess, "", self._on_lichess_button)
        self.lichess_button.pack(fill="x")
        self._lichess_progress: Optional[str] = None

        self.right_switch.set("Setup")
        self._show_right_view("Setup")
        self.variation_listbox.bind("<<ListboxSelect>>", lambda e: self._render_selected_plans())

    def _show_right_view(self, name: str) -> None:
        for view_name, view in self._right_views.items():
            if view_name == name:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()
        if name == TREE and self._tree is None:
            self._refresh_tree()

    # ---------- Tree view: the repertoire as a living tree (tree_view.py) ----------

    def _build_tree_view(self, parent) -> None:
        self._tree: Optional[opening_tree.Tree] = None
        self._tree_node: Optional[opening_tree.Node] = None
        self._tree_focus: list = []            # the line ◀ ▶ walk along
        self._draft: List[chess.Move] = []     # a new branch being played from _tree_node (not saved)
        self.tree_view = TreeView(parent, self._on_tree_select, on_wheel=self._tree_step)
        self.tree_view.on_deviation = self._on_deviation
        self.tree_view.on_context = self._on_tree_context
        self._start: Optional[opening_tree.Node] = None   # where the opening starts
        self._deviations: dict = {}            # id(node) -> [opening_tree.Deviation] from Lichess
        self.tree_view.pack(fill="both", expand=True)
        info = ctk.CTkFrame(parent, fg_color="transparent")
        info.pack(fill="x", pady=(10, 0))
        self.tree_line_label = theme.label(info, "", "heading", wraplength=380)
        self.tree_line_label.pack(anchor="w")
        self.tree_moves_label = theme.label(info, "", "muted", wraplength=380)
        self.tree_moves_label.pack(anchor="w", fill="x", pady=(2, 0))
        # Lichess players' replies your lines don't cover, at this position
        self.tree_dev_label = theme.label(info, "", "muted", wraplength=380, text_color="#9AA1AD")
        self.tree_dev_label.pack(anchor="w", fill="x", pady=(2, 4))
        self.show_before_var = tk.BooleanVar(value=db.get_setting(self.conn, "tree_show_before") != "0")
        ctk.CTkCheckBox(
            info, text="Show other openings (replies before yours starts)", variable=self.show_before_var,
            command=self._on_show_before, font=theme.FONT_SMALL, text_color=theme.TEXT_SOFT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER_STRONG,
            checkbox_width=16, checkbox_height=16,
        ).pack(anchor="w", pady=(0, 8))
        row = ctk.CTkFrame(info, fg_color="transparent")
        row.pack(fill="x")
        theme.button(row, "◀", lambda: self._tree_step(-1), width=44).pack(side="left")
        theme.button(row, "▶", lambda: self._tree_step(+1), width=44).pack(side="left", padx=(6, 0))
        self.tree_drill_button = theme.button(row, "Drill this line", self._drill_tree_line, "primary")
        self.tree_save_button = theme.button(row, "Save branch", self._save_draft, "primary")
        self.tree_discard_button = theme.button(row, "Discard", self._discard_draft, width=90)
        self._layout_tree_buttons()

    def _layout_tree_buttons(self) -> None:
        for b in (self.tree_drill_button, self.tree_save_button, self.tree_discard_button):
            b.pack_forget()
        if self._draft:
            self.tree_save_button.pack(side="left", fill="x", expand=True, padx=(8, 0))
            self.tree_discard_button.pack(side="left", padx=(6, 0))
        else:
            self.tree_drill_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _refresh_tree(self) -> None:
        """Rebuilds the tree for the repertoire picked in Setup."""
        selection = self._selected_repertoire()
        if selection is None:
            self._tree = None
            self.tree_view.set_tree(None, "")
            self.tree_line_label.configure(text="No repertoire yet")
            self.tree_moves_label.configure(text=f"Add some lines ({ADD_LINES}) to grow a tree.")
            return
        name, color = selection
        self._tree = opening_tree.build(self.conn, name, PlayerColor(color))
        self.tree_view.set_tree(self._tree, name)
        self._start = opening_tree.start_node(self.conn, self._tree, name, PlayerColor(color))
        self.tree_view.show_before = self.show_before_var.get()
        self.tree_view.set_start(self._start)
        self._update_deviations()
        self._tree_focus = self._tree.leaves[0][1] if self._tree.leaves else []
        self._show_tree_node(self._tree.root, follow=False)

    def _on_tree_select(self, node: opening_tree.Node) -> None:
        """A click in the tree: show that position on the board (this ends a
        drill in progress — Drill this line starts one from the tree)."""
        if self._draft and not messagebox.askyesno(
                "Discard new branch?", "You have a new branch that isn't saved. Discard it?", parent=self):
            return
        self._clear_draft()
        if self.session is not None and not self.session.is_finished():
            self.session = None
            self._layout_buttons()
            self.recap_label.configure(text="")
            self.move_note_label.configure(text="")
        lines = node.lines()
        # keep following the current line if the new position is on it
        on_focus = self._tree_focus and [n.move_uci for n in self._tree_focus[:node.ply]] == \
            [n.move_uci for n in node.path]
        if not on_focus and lines:
            self._tree_focus = lines[0][1]
        self._show_tree_node(node, follow=False)

    def _show_tree_node(self, node: opening_tree.Node, follow: bool) -> None:
        """follow: called from a live drill (the board is the drill's)."""
        self._tree_node = node
        self.tree_view.select(node, self._tree_focus)
        line = next((name for name, path, _ in (self._tree.leaves if self._tree else [])
                     if path == self._tree_focus), None)
        rep = self._selected_repertoire()
        self.tree_line_label.configure(text=short_name(line, rep[0]) if line and rep else "")
        board, sans = chess.Board(), []
        for n in node.path:
            move = chess.Move.from_uci(n.move_uci)
            prefix = f"{board.fullmove_number}." if board.turn == chess.WHITE else (
                f"{board.fullmove_number}..." if not sans else "")
            sans.append(prefix + board.san(move))
            board.push(move)
        lines = len(node.lines())
        where = " ".join(sans) if sans else "Starting position"
        self.tree_moves_label.configure(text=f"{where}   ·   {lines} line{'s' if lines != 1 else ''} from here")
        self._show_node_deviations(node)
        if follow:
            return
        flipped = rep is not None and rep[1] == PlayerColor.BLACK.value
        self.board_widget.show_board(board, flipped=flipped)
        self.line_name_label.configure(text=f"Exploring: {short_name(line, rep[0])}" if line and rep else "Exploring")
        note = node.move.notes if node.move is not None else None
        self.status_label.configure(text="Exploring the tree — Drill this line to practise it.",
                                    text_color=theme.TEXT_MUTED)
        self.move_note_label.configure(text=note or "")

    # ---------- Lichess deviations in the tree ----------

    def _update_deviations(self) -> None:
        """Popular Lichess replies your lines don't cover, from the cached
        explorer data (downloaded in the background — see _download_opening_data)."""
        selection = self._selected_repertoire()
        if self._tree is None or selection is None or not explorer.get_token(self.conn):
            self._deviations = {}
        else:
            bands = explorer.rating_bands(self.conn)
            self._deviations = opening_tree.deviations(
                self._tree, lambda fen: explorer.cached(self.conn, fen, bands), PlayerColor(selection[1]))
        self.tree_view.set_deviations(self._deviations)
        if self._tree_node is not None:
            self._show_node_deviations(self._tree_node)

    def _show_node_deviations(self, node: opening_tree.Node) -> None:
        if not explorer.get_token(self.conn):
            self.tree_dev_label.configure(
                text=f"Connect Lichess ({ADD_LINES} → Connect Lichess...) to see where players leave these lines.")
            return
        devs = self._deviations.get(id(node), [])
        before = self._start is not None and not opening_tree.is_inside(node, self._start)
        rep = self._selected_repertoire()
        if devs and before:
            self.tree_dev_label.configure(text=f"Before your {rep[0] if rep else 'opening'} starts — other "
                                               "openings Lichess players choose here: " + " · ".join(
                f"{d.san} {d.share:.0%}" for d in devs) + ".")
        elif devs:
            self.tree_dev_label.configure(text="Lichess players here also play: " + " · ".join(
                f"{d.san} {d.share:.0%}" for d in devs) + "  — click one in the tree to answer it.")
        else:
            total = sum(len(v) for v in self._deviations.values())
            self.tree_dev_label.configure(
                text=f"{total} popular deviation{'s' if total != 1 else ''} from these lines (grey rings — select one to see them)."
                if total else "No popular deviations found yet (data downloads in the background).")

    def _on_show_before(self) -> None:
        db.set_setting(self.conn, "tree_show_before", "1" if self.show_before_var.get() else "0")
        self.tree_view.show_before = self.show_before_var.get()
        self.tree_view.set_deviations(self._deviations)

    def _on_tree_context(self, node: opening_tree.Node, x: int, y: int) -> None:
        """Right-click in the tree: move where the opening starts."""
        selection = self._selected_repertoire()
        if selection is None or self._tree is None:
            return
        name, color = selection
        menu = tk.Menu(self, tearoff=0, bg=theme.PANEL_ALT, fg=theme.TEXT, activebackground=theme.CONTROL_BG,
                       activeforeground=theme.TEXT, font=theme.FONT_NATIVE)
        label = "the starting position" if node.move is None else tree_view_label(node)
        menu.add_command(label=f"{name} starts here (after {label})" if node.move else f"{name} starts at move 1",
                         command=lambda: self._set_opening_start(node))
        menu.add_command(label="Use the default start (where all lines still agree)",
                         command=lambda: self._set_opening_start(None))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_opening_start(self, node: Optional[opening_tree.Node]) -> None:
        name, color = self._selected_repertoire()
        opening_tree.set_start(self.conn, name, PlayerColor(color), node)
        self._start = opening_tree.start_node(self.conn, self._tree, name, PlayerColor(color))
        self.tree_view.set_start(self._start)
        if self._tree_node is not None:
            self._show_node_deviations(self._tree_node)

    def _on_deviation(self, node: opening_tree.Node, uci: str) -> None:
        """A wild twig clicked: start a new branch with that reply."""
        self._on_tree_select(node)
        if self._tree_node is not node:      # the user kept an unsaved branch
            return
        self._draft = [chess.Move.from_uci(uci)]
        self._show_draft()
        self.status_label.configure(text="Their reply is on the board — play your answer, then Save branch.",
                                    text_color=theme.TEXT_MUTED)

    def _tree_step(self, delta: int) -> None:
        """◀ / ▶ (or ← / →, or the wheel): back to the parent, or on along the
        current line. With a new branch: ◀ takes back its last move."""
        if self._tree is None or self._tree_node is None:
            return
        if self._draft:
            if delta < 0:
                self._draft.pop()
                self._show_draft() if self._draft else self._discard_draft()
            return
        node = self._tree_node
        if delta < 0:
            target = node.parent
        else:
            ply = node.ply
            nxt = self._tree_focus[ply] if ply < len(self._tree_focus) else None
            target = next((c for c in node.children if nxt is not None and c.move.move_uci == nxt.move_uci),
                          node.children[0] if node.children else None)
        if target is not None:
            self._on_tree_select(target)

    # ---------- growing a new branch from the tree ----------

    def _exploring(self) -> bool:
        """The board is showing a tree position (not a drill)."""
        return (self._tree is not None and self._tree_node is not None and self.right_switch.get() == TREE
                and (self.session is None or self.session.is_finished()))

    def _explore_move(self, move: chess.Move) -> None:
        """A move played on the board while exploring: follow it if it's in
        the repertoire, otherwise it starts (or extends) a new branch."""
        node = self._tree_node
        board = node.board()
        for m in self._draft:
            board.push(m)
        if move not in board.legal_moves:
            return
        if self.session is not None:          # a finished drill: back to exploring
            self.session = None
            self._layout_buttons()
        if not self._draft:
            child = next((c for c in node.children if c.move.move_uci == move.uci()), None)
            if child is not None:
                self._on_tree_select(child)
                return
        self._draft.append(move)
        self._show_draft()

    def _draft_sans(self) -> List[str]:
        """'7.b3', 'b6', '8.Bb2' — Black's move numbered only when it comes first."""
        board, sans = self._tree_node.board(), []
        for i, m in enumerate(self._draft):
            number = board.fullmove_number
            prefix = f"{number}." if board.turn == chess.WHITE else (f"{number}..." if i == 0 else "")
            sans.append(prefix + board.san(m))
            board.push(m)
        return sans

    def _show_draft(self) -> None:
        board = self._tree_node.board()
        for m in self._draft:
            board.push(m)
        self.board_widget.show_board(board)
        sans = self._draft_sans()
        self.tree_view.set_draft(self._tree_node, sans)
        self.tree_line_label.configure(text="New branch")
        self.tree_moves_label.configure(text=" ".join(sans))
        self.line_name_label.configure(text="Exploring: new branch")
        self.status_label.configure(text="New branch — play moves for both sides, then Save branch. "
                                         "◀ takes a move back.", text_color=theme.TEXT_MUTED)
        self.move_note_label.configure(text="")
        self._layout_tree_buttons()

    def _clear_draft(self) -> None:
        self._draft = []
        self.tree_view.draft_anchor, self.tree_view.draft_sans = None, []
        self._layout_tree_buttons()

    def _discard_draft(self) -> None:
        self._clear_draft()
        if self._tree_node is not None:
            self._show_tree_node(self._tree_node, follow=False)

    def _save_draft(self) -> None:
        """Adds the new branch to the repertoire as its own line: the moves
        to where it leaves the tree, then the new ones."""
        selection = self._selected_repertoire()
        if not self._draft or selection is None:
            return
        rep_name, color = selection
        node = self._tree_node
        board, sans = chess.Board(), []
        for m in [chess.Move.from_uci(n.move_uci) for n in node.path] + self._draft:
            sans.append(board.san(m))
            board.push(m)
        ucis = [m.uci() for m in board.move_stack]
        line = next((name for name, path, _ in self._tree.leaves if path == self._tree_focus), None)
        base = short_name(line, rep_name) if line else rep_name
        suggestion = f"{base} — {self._draft_sans()[0]}"
        dialog = ctk.CTkInputDialog(title="Save new branch",
                                    text=f"Name for this line (leave empty for “{suggestion}”):")
        name = dialog.get_input()
        if name is None:                      # cancelled: keep the branch
            return
        name = (name.strip() or suggestion).replace('"', "'")
        pgn = f'[Event "{rep_name} - {name}"]\n\n{format_move_list(sans)} *\n'
        opening_importer.import_pgn_text(pgn, rep_name, PlayerColor(color), self.conn)
        self._clear_draft()
        self._refresh_repertoire_list()       # regrows the tree with the new line
        if self._tree is None:
            self._refresh_tree()
        new = self._tree.node_for_path(ucis) if self._tree else None
        if new is not None:
            self._tree_focus = new.lines()[0][1] if new.lines() else self._tree_focus
            self._show_tree_node(new, follow=False)
        self.status_label.configure(text=f"Saved “{name}” to {rep_name}.", text_color=theme.SUCCESS)

    def _drill_tree_line(self) -> None:
        selection = self._selected_repertoire()
        if selection is None or not self._tree_focus:
            return
        name, color = selection
        self.session = OpeningDrillSession(self.conn, name, PlayerColor(color), move_share=self._move_share)
        if self.session.start_line(self._tree_focus):
            self._begin_line("line", keep_view=True)

    def _follow_drill_in_tree(self) -> None:
        """While drilling, the tree's marker moves along with the board."""
        if self._tree is None or self.session is None:
            return
        node = self._tree.node_for_path([m.uci() for m in self.session.board.move_stack])
        if node is not None:
            self._tree_focus = self.session.target_path
            self._show_tree_node(node, follow=True)

    def _render_selected_plans(self) -> None:
        """Plans for the variation highlighted in the list (before drilling)."""
        selection = self._selected_repertoire()
        if selection is None:
            return
        _, root, _ = self._selected_entry()
        self.preview_label.configure(text=self._line_preview(root))
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

    def _update_line_label(self) -> None:
        """Moves played so far in notation, or the whole line when 'Show full
        line' is on (hidden by default so it doesn't give answers away)."""
        if self.session is None or self.session.is_finished():
            return   # the end-of-line recap owns the label then
        if self._show_full_line:
            full = format_move_list(self.session.full_line_san())
            self.recap_label.configure(text=f"Full line: {full}")
        else:
            board = chess.Board()
            played = []
            for move in self.session.board.move_stack:
                played.append(board.san(move))
                board.push(move)
            self.recap_label.configure(text=f"Moves: {format_move_list(played)}" if played else "")

    def _toggle_full_line(self) -> None:
        self._set_full_line(not self._show_full_line)

    def _set_full_line(self, show: bool) -> None:
        """Show full line: the line as text, ◀ / ▶ to step through it on the
        board, and Same line / New random line to restart or skip."""
        self._show_full_line = show
        self.line_toggle.configure(text="Hide full line" if show else "Show full line")
        self._preview_index = None
        self._layout_buttons()
        self._render_board()
        self._update_line_label()

    def _layout_buttons(self) -> None:
        """Mid-line buttons under the board (one row, the board's width):
        Show/Hide full line, plus Same line + New random line while the full
        line is shown or after Show correct move, else Try again + Show
        correct move after a miss. The end of a line has its own set."""
        for button in (self.line_toggle, self.retry_button, self.reveal_button, self.same_line_button,
                       self.new_line_button, self.play_button, self.step_prev_button, self.step_next_button):
            button.pack_forget()
        if self.session is None or self.session.is_finished():
            return
        self.line_toggle.pack(side="left", padx=(0, 8))
        if self._show_full_line:
            self.step_next_button.pack(side="right")
            self.step_prev_button.pack(side="right", padx=(0, 6))
        if self._awaiting_retry and not self._show_full_line:
            self.retry_button.pack(side="left")
            self.reveal_button.pack(side="left", padx=(8, 0))
        elif self._show_full_line or self._offer_restart:
            self.same_line_button.pack(side="left")
            self.new_line_button.pack(side="left", padx=(8, 0))

    def _render_board(self) -> None:
        """The drill position, or the full-line position being stepped through."""
        if self.session is None:
            return
        if self._preview_index is None:
            self.board_widget.show_board(self.session.board)
            self._follow_drill_in_tree()
            return
        board = chess.Board()
        for node in self.session.target_path[:self._preview_index]:
            board.push(chess.Move.from_uci(node.move_uci))
        self.board_widget.show_board(board)

    def _step_preview(self, delta: int) -> None:
        if self.session is None or not self._show_full_line:
            return
        live = len(self.session.board.move_stack)
        current = live if self._preview_index is None else self._preview_index
        index = max(0, min(len(self.session.target_path), current + delta))
        self._preview_index = None if index == live else index
        self._render_board()
        if self._preview_index is None:
            self.status_label.configure(text="Back to the drill — your move." if self.session.is_my_turn()
                                        else "Back to the drill.", text_color=theme.TEXT)
            return
        sans = self.session.full_line_san()
        last = f"{(index + 1) // 2}.{'' if index % 2 else '..'} {sans[index - 1]}" if index else ""
        self.status_label.configure(
            text=(f"Viewing the line: {'after ' + last if index else 'starting position'} "
                  f"({index}/{len(sans)}). Step back to where you were, or Hide full line, "
                  f"to keep drilling."),
            text_color=theme.TEXT_MUTED)

    def _on_arrow_key(self, delta: int) -> None:
        if not self.winfo_ismapped():
            return
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "Text"):
            return
        if self._show_full_line:
            self._step_preview(delta)
        elif self.right_switch.get() == TREE and (self.session is None or self.session.is_finished()):
            self._tree_step(delta)

    def _line_preview(self, root) -> str:
        """Notation for a variation in the Setup list: its first branch at
        each step, noting how many alternative branches it has."""
        if root is None:
            return ""
        path, node, branches = [root], root, 0
        while True:
            children = db.children_of(self.conn, node.id)
            if not children:
                break
            branches += len(children) - 1
            node = children[0]
            path.append(node)
        board, sans = chess.Board(), []
        for n in path:
            move = chess.Move.from_uci(n.move_uci)
            sans.append(board.san(move))
            board.push(move)
        extra = f"  (+{branches} alternative branch{'es' if branches != 1 else ''})" if branches else ""
        return format_move_list(sans) + extra

    def _show_move_note(self, san: Optional[str]) -> None:
        """The imported PGN's comment on the move just played, if any."""
        self._update_line_label()
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
                    text="No repertoires yet. Import a PGN file or paste a line "
                         f"({ADD_LINES}, on the right) to begin.",
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
        self._download_opening_data()
        self._tree = None                      # repertoire or its lines changed: regrow the tree
        if self.right_switch.get() == TREE:
            self._refresh_tree()

    # ---------- Lichess: real-world opponent replies ----------

    def _on_lichess_button(self) -> None:
        if explorer.get_token(self.conn):
            lichess_ui.disconnect(self, self.conn, self._on_lichess_changed)
        else:
            lichess_ui.connect_dialog(self, self.conn, self._on_lichess_changed)

    def _on_lichess_changed(self) -> None:
        self._refresh_lichess_card()
        self._download_opening_data()

    def _refresh_lichess_card(self) -> None:
        if not explorer.get_token(self.conn):
            self.lichess_label.configure(
                text="Not connected: when your lines branch, the computer picks the opponent's "
                     "reply at random. Connect Lichess to have it reply as often as real "
                     "players at your level do.")
            self.lichess_button.configure(text="Connect Lichess...")
            return
        bands = explorer.rating_bands(self.conn)
        text = (f"Connected: the computer picks the opponent's replies as often as Lichess "
                f"players {explorer.describe_bands(bands)} play them (blitz and rapid).")
        if self._lichess_progress:
            text += f"\n{self._lichess_progress}"
        self.lichess_label.configure(text=text)
        self.lichess_button.configure(text="Disconnect Lichess")

    def _download_opening_data(self) -> None:
        """Fetches Lichess stats for every opponent position in the selected
        repertoire that isn't cached yet (in the background)."""
        selection = self._selected_repertoire()
        if selection is None or not explorer.get_token(self.conn):
            self._refresh_lichess_card()
            return
        fens = explorer.opponent_positions(self.conn, selection[0])

        def on_progress(done, total):
            self._lichess_progress = f"Downloading opening data: {done} / {total} positions…"
            self._refresh_lichess_card()

        def on_done(fetched, error):
            self._lichess_progress = f"Couldn't download opening data: {error}" if error else None
            self._refresh_lichess_card()
            if fetched and self._tree is not None:
                self._update_deviations()            # new data: new wild twigs

        lichess_ui.downloader.start(self, self.conn, fens, on_done, on_progress)

    def _move_share(self, fen: str, uci: str) -> Optional[float]:
        """Share of Lichess games (at your level) with this move here; None if unknown."""
        if not explorer.get_token(self.conn):
            return None
        stats = explorer.cached(self.conn, fen)
        return stats.share(uci) if stats is not None and stats.games else None

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
        self.session = OpeningDrillSession(self.conn, name, my_color, move_share=self._move_share)
        if not self.session.start(self._selected_root_id()):
            messagebox.showinfo("Empty repertoire", "That repertoire has no lines in it yet.")
            return
        self._begin_line(kind)

    def _drill_same_line(self) -> None:
        if self.session is None:
            return
        self.session.restart()
        kind, _, _ = self._selected_entry()
        self._begin_line(kind)

    def _drill_new_random_line(self) -> None:
        """A random line from the whole repertoire — a different one than
        just finished, when the repertoire has more than one."""
        if self.session is None:
            return
        previous = [n.id for n in self.session.target_path]
        session = OpeningDrillSession(self.conn, self.session.repertoire_name, self.session.my_color,
                                      move_share=self._move_share)
        for _ in range(10):
            if not session.start():
                return
            if [n.id for n in session.target_path] != previous:
                break
        self.session = session
        # reflect it in Setup: "Any line (random)"
        self.variation_listbox.selection_clear(0, tk.END)
        self.variation_listbox.selection_set(0)
        self._begin_line("random")

    def _begin_line(self, kind: str, keep_view: bool = False) -> None:
        """Resets the board and panels for self.session's line and starts it.
        keep_view: stay on the current right-hand view (e.g. the Tree)."""
        name, my_color = self.session.repertoire_name, self.session.my_color
        self._line_missed = False
        self._preview_index = None
        self._awaiting_retry = False
        self._offer_restart = False
        self.recap_label.configure(text="")
        self._show_full_line = False
        self.line_toggle.configure(text="Show full line")
        self._layout_buttons()
        self.move_note_label.configure(text="")
        self._render_plans(self.session.line_name, name, my_color)
        if not keep_view:
            self.right_switch.set("Plans")
            self._show_right_view("Plans")
        self._hide_retry_buttons()
        self.start_button.configure(
            text={"random": "New random line", "weak": "Next weak line"}.get(kind, "Drill again")
        )
        self.line_name_label.configure(text=f"Drilling: {self.session.line_name}")
        # show the board from your side when drilling Black
        self.board_widget.show_board(self.session.board, flipped=my_color == PlayerColor.BLACK)
        self._update_line_label()
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
            self._render_board()
            self.status_label.configure(text="Your move.", text_color=theme.TEXT)
            return

        fen = self.session.board.fen()
        uci = self.session.target_path[self.session.path_index].move_uci
        san = self.session.play_opponent_move()
        self._render_board()
        share = self._move_share(fen, uci)
        how_often = f" ({share:.0%} of players at your level play it)" if share is not None else ""
        self.status_label.configure(text=f"Opponent played {san}{how_often}. Your move.",
                                    text_color=theme.TEXT)
        self._show_move_note(san)
        self.after(300, self._advance)

    def _show_completion(self) -> None:
        recap = format_move_list(self.session.full_line_san())
        self.status_label.configure(text="End of line — nice work. Go again, or try a new random line.",
                                 text_color=theme.SUCCESS)
        self.recap_label.configure(text=f"{self.session.line_name}:\n{recap}")
        self._awaiting_retry = False
        self._show_full_line = False
        self._preview_index = None
        self._layout_buttons()          # finished: clears the mid-line buttons
        self._render_board()
        # one attempt per line: clean run = correct (clears it from Weak lines over time)
        db.record_attempt(self.conn, "line", self.session.target_path[0].id, not self._line_missed)
        progression.notify()
        self.same_line_button.pack(side="left")
        self.new_line_button.pack(side="left", padx=(8, 0))
        self.play_button.pack(side="left", padx=(8, 0))

    def _play_it_out(self) -> None:
        """Continue the finished line as a real game against Stockfish."""
        if self.session is None:
            return
        color = chess.WHITE if self.session.my_color == PlayerColor.WHITE else chess.BLACK
        PlayWindow(self, self.session.board.fen(), title=f"Play it out — {self.session.line_name}",
                   my_color=color)

    def _on_my_move_attempt(self, move: chess.Move) -> None:
        if self._exploring():
            self._explore_move(move)
            return
        if self._preview_index is not None:
            self._render_board()        # viewing another position — not a drill move
            return
        if self.session is None or not self.session.is_my_turn():
            return
        result: DrillResult = self.session.attempt_my_move(move)
        self._render_board()

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
        self._awaiting_retry = True
        self._layout_buttons()

    def _hide_retry_buttons(self) -> None:
        self._awaiting_retry = False
        self._layout_buttons()

    def _on_retry(self) -> None:
        self._hide_retry_buttons()
        self.status_label.configure(text="Your move — try again.", text_color=theme.TEXT)

    def _on_reveal(self) -> None:
        if self.session is None:
            return
        self._line_missed = True
        self._offer_restart = True
        san = self.session.reveal_and_advance()
        self._render_board()
        self._hide_retry_buttons()
        if san is None:
            # shouldn't normally happen (reveal is only offered mid-line),
            # but avoid showing a broken "Correct move was None" message
            self.status_label.configure(text="Nothing left to reveal.", text_color=theme.TEXT)
            self._advance()
            return
        self.status_label.configure(
            text=f"Correct move was {san}. Keep going, or start over with Same line.", text_color=theme.TEXT)
        self._show_move_note(san)
        self.after(700, self._advance)
