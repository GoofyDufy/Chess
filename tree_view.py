"""The "living tree" drawing of a repertoire (layout in opening_tree.py):
bark-coloured branches growing up from the starting position, thicker where
more lines run, a bead for every move, a knot where lines part and a
coloured leaf, with its name, where each line ends. The selected position
glows amber back to the root. Moving the mouse snaps to the nearest move
(ring + name) and a click picks it; the wheel steps along the line. Positions
where Lichess players often leave your lines get a thin grey ring; the
selected one shows those replies as clickable grey twigs. A new branch
being played on the board (not saved yet) sprouts as a dashed amber twig. Wider than the view when there are many lines: it scrolls (Shift +
wheel)."""

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

import chess
import customtkinter as ctk

import opening_tree
import theme
from opening_tree import Branch, Node, Tree

BARK = "#4E4434"
BEAD = "#8A7E68"
GLOW = theme.ACCENT
NAME_CHARS = 20          # leaf names longer than this are shortened (the full name shows below the tree)
SNAP_PX = 24             # the mouse picks the nearest move within this distance
DRAFT_STEP = (15, -17)   # each move of a new (unsaved) branch: right and up from the last
WILD = "#6B7280"         # Lichess deviations: replies your lines don't cover
WILD_BEFORE = "#3F4450"  # ...before the opening starts (other openings): fainter
WILD_BEFORE_TEXT = "#6B7280"
LABEL_AT = 0.78          # branch labels sit this far along their branch, where neighbours have spread apart
LEAF_COLORS = ["#E6A94A", "#E8735E", "#D6789E", "#CBB26A", "#4FB3A5",
               "#5B8DEF", "#8FC7F0", "#7DCB98", "#9C8BE0"]


def short_name(name: str, repertoire: str) -> str:
    """'Catalan - Closed' -> 'Closed' inside the Catalan repertoire."""
    for sep in (" - ", " — ", ": "):
        head, _, tail = name.partition(sep)
        if tail and head.strip().lower() == repertoire.strip().lower():
            return tail.strip()
    return name


def move_label(node: Node) -> str:
    """'4...Be7' for the move that reached this position."""
    board = node.parent.board() if node.parent else chess.Board()
    move = chess.Move.from_uci(node.move.move_uci)
    number = board.fullmove_number
    prefix = f"{number}." if board.turn == chess.WHITE else f"{number}..."
    return prefix + board.san(move)


class TreeView(ctk.CTkFrame):
    def __init__(self, master, on_select: Callable[[Node], None], height: int = 420,
                 on_wheel: Optional[Callable[[int], None]] = None):
        super().__init__(master, fg_color=theme.PANEL_BG, corner_radius=theme.RADIUS)
        self.on_select = on_select
        self.on_wheel = on_wheel
        self.on_deviation: Optional[Callable[[Node, str], None]] = None   # (position, uci)
        self.deviations: Dict[int, list] = {}      # id(node) -> [opening_tree.Deviation]
        self._dev_items: Dict[int, Tuple[Node, str]] = {}
        self.start: Optional[Node] = None          # where the opening starts (opening_tree.start_node)
        self.show_before = True                     # draw replies before the start (other openings)
        self.on_context: Optional[Callable[[Node, int, int], None]] = None   # right-click (node, x, y)
        self.canvas = tk.Canvas(self, bg=theme.PANEL_BG, highlightthickness=0, height=height)
        self.scroll = ctk.CTkScrollbar(self, orientation="horizontal", command=self.canvas.xview,
                                       button_color=theme.CONTROL_BG, fg_color="transparent")
        self.canvas.configure(xscrollcommand=self.scroll.set)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.tree: Optional[Tree] = None
        self.repertoire = ""
        self.selected: Optional[Node] = None
        self.focus_path: List = []           # the line ◀ ▶ follow, highlighted past the selection
        self._items: Dict[int, Node] = {}    # canvas item -> position
        self._branch_items: Dict[int, Branch] = {}
        self._node_branch: Dict[int, Tuple[Branch, int]] = {}
        self._size = (0, 0)
        self._hovered: Optional[Node] = None
        self.draft_anchor: Optional[Node] = None   # a new branch being played, from this position
        self.draft_sans: List[str] = []
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<MouseWheel>", lambda e: self._wheel(-1 if e.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda e: self._wheel(-1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(1))
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Shift-Button-4>", lambda e: self.canvas.xview_scroll(-1, "units"))
        self.canvas.bind("<Shift-Button-5>", lambda e: self.canvas.xview_scroll(1, "units"))

    # ---------- public ----------

    def set_tree(self, tree: Optional[Tree], repertoire: str) -> None:
        self.tree, self.repertoire = tree, repertoire
        self.selected = tree.root if tree else None
        self.focus_path = tree.leaves[0][1] if tree and tree.leaves else []
        self._relayout()

    def set_start(self, node: Optional[Node]) -> None:
        self.start = node
        self._paint()

    def _on_right_click(self, event) -> None:
        node = self._node_at(event)
        if node is not None and self.on_context is not None:
            self.on_context(node, event.x_root, event.y_root)

    def set_deviations(self, deviations: Dict[int, list]) -> None:
        self.deviations = deviations
        self._paint()

    def set_draft(self, anchor: Optional[Node], sans: List[str]) -> None:
        """Show (or clear, with anchor None) a new branch being played."""
        self.draft_anchor, self.draft_sans = anchor, list(sans)
        self._paint()

    def select(self, node: Optional[Node], focus_path: Optional[List] = None) -> None:
        self.selected = node
        if focus_path is not None:
            self.focus_path = focus_path
        self._paint()
        self._scroll_into_view(node)

    # ---------- drawing ----------

    def _on_resize(self, event) -> None:
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self._relayout()

    def _relayout(self) -> None:
        if self.tree is None:
            self.canvas.delete("all")
            return
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        opening_tree.layout(self.tree, w, h)
        self._node_branch = {id(n): (b, i) for b in self.tree.branches for i, n in enumerate(b.nodes)}
        if self.tree.width > w + 1:
            self.scroll.pack(fill="x", padx=6, pady=(0, 4))
        else:
            self.scroll.pack_forget()
        self.canvas.configure(scrollregion=(0, 0, self.tree.width, h))
        self._paint()

    def _on_path(self) -> set:
        """ids of the positions from the root to the selection."""
        ids, node = set(), self.selected
        while node is not None:
            ids.add(id(node))
            node = node.parent
        return ids

    def _paint(self) -> None:
        c, tree = self.canvas, self.tree
        c.delete("all")
        self._items, self._branch_items = {}, {}
        if tree is None:
            return
        root = tree.root
        c.create_oval(root.x - 120, root.y + 8, root.x + 120, root.y + 26, fill="#1C1F25", outline="")
        on_path = self._on_path()
        focus_ids = set()
        if self.focus_path:
            node = tree.node_for_path([n.move_uci for n in self.focus_path])
            while node is not None:
                focus_ids.add(id(node))
                node = node.parent

        # bark: thick trunks first, so thinner twigs lie on top
        for b in sorted(tree.branches, key=lambda b: -b.lines):
            x0, y0 = b.start_xy
            pts = opening_tree.curve(x0, y0, b.end.x, b.end.y)
            item = c.create_line(*pts, smooth="raw", splinesteps=24, width=opening_tree.thickness(b.lines),
                                 fill=BARK, capstyle="round")
            self._branch_items[item] = b
        # trunk under the root
        c.create_line(root.x, root.y, root.x, root.y + 14, width=opening_tree.thickness(len(tree.leaves)),
                      fill=BARK, capstyle="round")

        # glow: the selected path solid, the rest of the focus line dashed
        for b in tree.branches:
            x0, y0 = b.start_xy
            n = len(b.nodes)
            solid = [i for i, node in enumerate(b.nodes) if id(node) in on_path]
            ahead = [i for i, node in enumerate(b.nodes) if id(node) in focus_ids and id(node) not in on_path]
            if solid:
                self._glow_segment(b, 0.0, (max(solid) + 1) / n, dash=None)
            if ahead:
                start = (max(solid) + 1) / n if solid else 0.0
                self._glow_segment(b, start, 1.0, dash=(3, 6))

        # beads (every move), knots, leaves
        for b in tree.branches:
            for node in b.nodes[:-1]:
                self._bead(node)
            self._knot_or_leaf(b)
        self._label_branches()
        c.create_oval(root.x - 8, root.y - 8, root.x + 8, root.y + 8, fill=theme.PANEL_BG,
                      outline=theme.TEXT, width=2.5)
        c.create_text(root.x, root.y + 22, text="Start", fill=theme.TEXT, font=(theme.FONT_FAMILY, 11, "bold"))
        self._paint_deviations()
        self._paint_start_flag()
        if self.draft_anchor is not None:
            self._paint_draft()
        else:
            self._marker()
        # the snapping hover: a ring on the nearest move and its name
        self._hover_ring = c.create_oval(0, 0, 0, 0, outline=theme.TEXT, width=2, state="hidden")
        self._hover_bg = c.create_rectangle(0, 0, 0, 0, fill=theme.CONTROL_BG, outline="", state="hidden")
        self._hover_text = c.create_text(0, 0, text="", anchor="w", fill=theme.TEXT,
                                         font=(theme.FONT_FAMILY, 10, "bold"), state="hidden")
        self._hovered = None

    def _glow_segment(self, b: Branch, t0: float, t1: float, dash) -> None:
        if t1 <= t0:
            return
        x0, y0 = b.start_xy
        steps = max(2, int(24 * (t1 - t0)))
        pts = []
        for k in range(steps + 1):
            x, y = opening_tree.bezier(x0, y0, b.end.x, b.end.y, t0 + (t1 - t0) * k / steps)
            pts += [x, y]
        self.canvas.create_line(*pts, width=4, fill=GLOW, capstyle="round", smooth=True, dash=dash)

    def _bead(self, node: Node) -> None:
        r = 2.6
        item = self.canvas.create_oval(node.x - r, node.y - r, node.x + r, node.y + r, fill=BEAD, outline="")
        self._clickable(item, node)

    def _knot_or_leaf(self, b: Branch) -> None:
        c, end = self.canvas, b.end
        if b.children:
            item = c.create_oval(end.x - 7, end.y - 7, end.x + 7, end.y + 7, fill=theme.PANEL_BG,
                                 outline=theme.TEXT, width=2.5)
            self._clickable(item, end)
        if end.line_ends:
            color = LEAF_COLORS[b.color_index % len(LEAF_COLORS)]
            tilt = -25 if b.color_index % 2 == 0 else 25
            if b.children:                         # a line that stops at a knot: a leaf beside it
                lx, ly, tilt = end.x - 12, end.y - 6, -60
            else:
                lx, ly = end.x, end.y
            item = c.create_polygon(*self._leaf_shape(lx, ly, tilt), smooth=True, fill=color,
                                    outline="#111317", width=1)
            self._clickable(item, end)
            name = short_name(end.line_ends[0][0], self.repertoire)
            if len(name) > NAME_CHARS:
                name = name[:NAME_CHARS - 1].rstrip(" ,(") + "…"
            if len(end.line_ends) > 1:
                name += f" +{len(end.line_ends) - 1}"
            tid = c.create_text(lx + 4, ly - 18, text=name, anchor="w", angle=40, fill=theme.TEXT,
                                font=(theme.FONT_FAMILY, 10, "bold"))
            self._clickable(tid, end)

    @staticmethod
    def _leaf_shape(x: float, y: float, tilt_deg: float) -> List[float]:
        a = math.radians(tilt_deg - 90)          # 0 = straight up
        ux, uy = math.cos(a), math.sin(a)
        px, py = -uy, ux
        length, half = 18, 6
        tip = (x + ux * length, y + uy * length)
        mid = (x + ux * length / 2, y + uy * length / 2)
        return [x, y, mid[0] + px * half, mid[1] + py * half, tip[0], tip[1],
                mid[0] - px * half, mid[1] - py * half]

    def _label_branches(self) -> None:
        """Each branch's first move, a little way along it — where branches
        leaving the same knot have already spread apart."""
        for b in self.tree.branches:
            x0, y0 = b.start_xy
            x, y = opening_tree.bezier(x0, y0, b.end.x, b.end.y, LABEL_AT if len(b.nodes) > 1 else 0.5)
            self.canvas.create_text(x + 8, y, text=move_label(b.nodes[0]), anchor="w", fill=theme.TEXT_MUTED,
                                    font=(theme.FONT_FAMILY, 9))

    def _marker(self) -> None:
        node = self.selected
        if node is None:
            return
        c = self.canvas
        c.create_oval(node.x - 10, node.y - 10, node.x + 10, node.y + 10, outline=GLOW, width=3.5,
                      fill=theme.BG)
        c.create_oval(node.x - 3.5, node.y - 3.5, node.x + 3.5, node.y + 3.5, fill=GLOW, outline="")

    def _clickable(self, item: int, node: Node) -> None:
        """Leaves and their names: pointing at them picks the line's end (they
        can sit away from the move itself, so snapping wouldn't reach them)."""
        self._items[item] = node

    # ---------- hover, click, wheel ----------

    def _nodes(self) -> List[Node]:
        if self.tree is None:
            return []
        return [self.tree.root] + [n for b in self.tree.branches for n in b.nodes]

    def nearest(self, x: float, y: float) -> Optional[Node]:
        best, best_d = None, SNAP_PX ** 2
        for node in self._nodes():
            d = (node.x - x) ** 2 + (node.y - y) ** 2
            if d <= best_d:
                best, best_d = node, d
        return best

    def _node_at(self, event) -> Optional[Node]:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        over = self.canvas.find_withtag("current")
        if over and over[0] in self._items:
            return self._items[over[0]]
        return self.nearest(x, y)

    def _on_motion(self, event) -> None:
        over = self.canvas.find_withtag("current")
        if over and over[0] in self._dev_items:          # a wild twig: no move snapping here
            self._set_hover(None)
            self.canvas.configure(cursor="hand2")
            return
        self._set_hover(self._node_at(event))

    def _set_hover(self, node: Optional[Node]) -> None:
        if node is self._hovered or self.tree is None:
            return
        self._hovered = node
        c = self.canvas
        if node is None:
            for item in (self._hover_ring, self._hover_bg, self._hover_text):
                c.itemconfigure(item, state="hidden")
            c.configure(cursor="")
            return
        r = 7
        c.coords(self._hover_ring, node.x - r, node.y - r, node.x + r, node.y + r)
        text = "Start" if node.move is None else move_label(node)
        right = node.x < self.tree.width - 80
        tx = node.x + 13 if right else node.x - 13
        c.itemconfigure(self._hover_text, text=text, anchor="w" if right else "e", state="normal")
        c.coords(self._hover_text, tx, node.y)
        x0, y0, x1, y1 = c.bbox(self._hover_text)
        c.coords(self._hover_bg, x0 - 5, y0 - 2, x1 + 5, y1 + 2)
        for item in (self._hover_ring, self._hover_bg):
            c.itemconfigure(item, state="normal")
        c.tag_raise(self._hover_ring)
        c.tag_raise(self._hover_bg)
        c.tag_raise(self._hover_text)
        c.configure(cursor="hand2")

    def _on_click(self, event) -> None:
        over = self.canvas.find_withtag("current")
        if over and over[0] in self._dev_items and self.on_deviation is not None:
            self.on_deviation(*self._dev_items[over[0]])
            return
        node = self._node_at(event)
        if node is not None:
            self.on_select(node)

    def _wheel(self, delta: int) -> None:
        if self.on_wheel is not None:
            self.on_wheel(delta)

    # ---------- Lichess deviations: short grey "wild" twigs ----------

    def _paint_deviations(self) -> None:
        c = self.canvas
        self._dev_items = {}
        if self.tree is None:
            return
        by_id = {id(n): n for n in self._nodes()}
        for node_id, devs in self.deviations.items():
            node = by_id.get(node_id)
            if node is None:
                continue
            inside = self.start is None or opening_tree.is_inside(node, self.start)
            if not inside and not self.show_before:
                continue
            line_color, text_color = (WILD, "#9AA1AD") if inside else (WILD_BEFORE, WILD_BEFORE_TEXT)
            if node is not self.selected:
                # elsewhere just a thin ring: "Lichess players leave your lines here" —
                # the twigs (with names, clickable) show for the selected position only
                c.create_oval(node.x - 6, node.y - 6, node.x + 6, node.y + 6, outline=line_color, width=1.5)
                continue
            for i, dev in enumerate(devs):
                side = -1 if i % 2 == 0 else 1          # alternate left / right of the branch
                step = i // 2
                if node is self.start:                  # the start flag is on the right: fan left
                    side, step = -1, i
                if node.x < 70:
                    side = 1
                elif node.x > self.tree.width - 70:
                    side = -1
                ex, ey = node.x + side * (22 + 8 * step), node.y - 12 - 10 * step
                c.create_line(node.x, node.y, ex, ey, fill=line_color, width=2, dash=(2, 3))
                ring = c.create_oval(ex - 4, ey - 4, ex + 4, ey + 4, outline=line_color, width=2,
                                     fill=theme.PANEL_BG)
                text = f"{dev.san.split('.')[-1]} {dev.share:.0%}"
                label = c.create_text(ex + side * 7, ey, text=text, anchor="w" if side > 0 else "e",
                                      fill=text_color, font=(theme.FONT_FAMILY, 8))
                for item in (ring, label):
                    self._dev_items[item] = (node, dev.uci)

    def _paint_start_flag(self) -> None:
        """A small amber flag where the opening starts (not at the very start)."""
        node = self.start
        if node is None or node.move is None:
            return
        c = self.canvas
        right = node.x < self.tree.width - 120
        fx = node.x + (10 if right else -10)
        c.create_line(fx, node.y - 2, fx, node.y - 22, fill=GLOW, width=2)
        c.create_polygon(fx, node.y - 22, fx + (12 if right else -12), node.y - 18, fx, node.y - 14,
                         fill=GLOW, outline="")
        c.create_text(fx + (16 if right else -16), node.y - 18, text=f"{self.repertoire} starts",
                      anchor="w" if right else "e", fill=GLOW, font=(theme.FONT_FAMILY, 9, "bold"))

    # ---------- a new branch being played (not saved yet) ----------

    def _paint_draft(self) -> None:
        c, anchor = self.canvas, self.draft_anchor
        dx, dy = DRAFT_STEP
        if anchor.x > self.tree.width - 160:          # grow toward the side with room
            dx = -dx
        pts = [anchor.x, anchor.y]
        for k in range(1, len(self.draft_sans) + 1):
            pts += [anchor.x + dx * k, anchor.y + dy * k]
        if len(pts) >= 4:
            c.create_line(*pts, width=4, fill=GLOW, dash=(4, 4), capstyle="round")
        for k in range(1, len(self.draft_sans) + 1):
            x, y = anchor.x + dx * k, anchor.y + dy * k
            c.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, fill=GLOW, outline="")
        tip_x, tip_y = pts[-2], pts[-1]
        c.create_oval(tip_x - 10, tip_y - 10, tip_x + 10, tip_y + 10, outline=GLOW, width=3.5, fill=theme.BG)
        c.create_oval(tip_x - 3.5, tip_y - 3.5, tip_x + 3.5, tip_y + 3.5, fill=GLOW, outline="")
        label = "New branch" + (f": {' '.join(self.draft_sans[-3:])}" if self.draft_sans else "")
        c.create_text(tip_x + (14 if dx > 0 else -14), tip_y, text=label, anchor="w" if dx > 0 else "e",
                      fill=GLOW, font=(theme.FONT_FAMILY, 10, "bold"))

    def _scroll_into_view(self, node: Optional[Node]) -> None:
        if node is None or self.tree is None or self.tree.width <= self.canvas.winfo_width() + 1:
            return
        left, right = (f * self.tree.width for f in self.canvas.xview())
        if not left + 30 <= node.x <= right - 30:
            self.canvas.xview_moveto(max(0.0, (node.x - (right - left) / 2) / self.tree.width))
