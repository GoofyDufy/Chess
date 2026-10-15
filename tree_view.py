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
import tkinter.font as tkfont
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
# from your own games (stars — circles are Lichess data): where you / your opponents left your prep
MY_DEVIATION = "#D9534F"   # red star: you
OPP_DEVIATION = "#3B82F6"  # blue star: your opponent
WILD = "#6B7280"         # Lichess deviations: replies your lines don't cover
WILD_BEFORE = "#3F4450"  # ...before the opening starts (other openings): fainter
# how often Lichess players at your level choose a sideline: grey / amber / red
LIKELY = [(0.30, "#E8735E", "#F2A493"), (0.15, "#E6A94A", "#F0C27A"), (0.0, "#8A8F98", "#B4B9C2")]
LIKELY_TEXT = "grey under 15% · amber 15–30% · red 30%+"


def likely_colors(share: float) -> Tuple[str, str]:
    """(line / ring colour, label colour) for a sideline played this often."""
    return next((line, label) for floor, line, label in LIKELY if share >= floor)
WILD_BEFORE_TEXT = "#6B7280"
LABEL_AT = 0.78          # branch labels sit this far along their branch, where neighbours have spread apart
LEAF_COLORS = ["#E6A94A", "#E8735E", "#D6789E", "#CBB26A", "#4FB3A5",
               "#5B8DEF", "#8FC7F0", "#7DCB98", "#9C8BE0"]


def short_name(name: str, repertoire: str) -> str:
    """'Catalan - Closed' -> 'Closed' inside the Catalan repertoire; with
    every repertoire on show (repertoire ''), 'Catalan · Closed'."""
    for sep in (" - ", " — ", ": "):
        head, _, tail = name.partition(sep)
        if tail and head.strip().lower() == repertoire.strip().lower():
            return tail.strip()
        if tail and not repertoire:
            return f"{head.strip()} · {tail.strip()}"
    return name


GLYPHS = {chess.KING: "♚", chess.QUEEN: "♛", chess.ROOK: "♜", chess.BISHOP: "♝", chess.KNIGHT: "♞",
          chess.PAWN: "♟"}


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
        # from your games, as numbered stars: where YOU left your prep (red,
        # id(node) -> [my_openings.MyDeviation]) and where your OPPONENT did (blue,
        # [my_openings.OpponentDeviation]); clicking one calls on_my_deviation(deviation)
        self.my_deviations: Dict[int, list] = {}
        self.opp_deviations: Dict[int, list] = {}
        self.on_my_deviation: Optional[Callable] = None
        self._my_items: Dict[int, object] = {}
        self.starts: List[Tuple[str, Node]] = []   # (repertoire, where its opening starts)
        self._board_items: Dict[int, Node] = {}     # mini-board square -> its first move (or the root)
        self.show_before = True                     # draw replies before the start (other openings)
        self.zoom = True                            # zoom in on the branch you're on (see _zoom_branch)
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
        # free zoom, any time (Ctrl+wheel / + − buttons, drag to move): a view on top
        # of the layout — moves spread out, text and stars keep their size
        self.view_zoom = 1.0
        self.view_center: Optional[Tuple[float, float]] = None   # layout point at the canvas centre
        self._press: Optional[Tuple[int, int]] = None
        self._dragged = False
        self._build_zoom_buttons()
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Control-MouseWheel>", lambda e: self.zoom_at(e.x, e.y, 1.25 if e.delta > 0 else 0.8))
        self.canvas.bind("<Control-Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.25))
        self.canvas.bind("<Control-Button-5>", lambda e: self.zoom_at(e.x, e.y, 0.8))
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
        self.view_zoom, self.view_center = 1.0, None
        self.selected = tree.root if tree else None
        self.focus_path = tree.leaves[0][1] if tree and tree.leaves else []
        self._relayout()

    def set_starts(self, starts: List[Tuple[str, Node]]) -> None:
        self.starts = list(starts)
        self._paint()

    def inside(self, node: Node) -> bool:
        """At or after where one of the openings starts (no starts set: everywhere)."""
        return not self.starts or any(opening_tree.is_inside(node, start) for _, start in self.starts)

    def _on_right_click(self, event) -> None:
        node = self._node_at(event)
        if node is not None and self.on_context is not None:
            self.on_context(node, event.x_root, event.y_root)

    def set_deviations(self, deviations: Dict[int, list]) -> None:
        self.deviations = deviations
        self._paint()

    def set_my_deviations(self, deviations: Dict[int, list], opponents: Optional[Dict[int, list]] = None) -> None:
        self.my_deviations = deviations
        self.opp_deviations = opponents or {}
        self._paint()

    def set_draft(self, anchor: Optional[Node], sans: List[str]) -> None:
        """Show (or clear, with anchor None) a new branch being played."""
        self.draft_anchor, self.draft_sans = anchor, list(sans)
        self._paint()

    def select(self, node: Optional[Node], focus_path: Optional[List] = None) -> None:
        """Inside a first move's tree, only that tree shows; at the start, all of them."""
        self.selected = node
        if focus_path is not None:
            self.focus_path = focus_path
        focus = self.tree.first_move_of(node) if self.tree is not None and node is not None else None
        if self.tree is not None and (focus is not self.tree.focus or self._zoom_branch() is not self.tree.zoom):
            self._relayout()
        else:
            self._paint()
        self._scroll_into_view(node)

    # ---------- drawing ----------

    def _zoom_branch(self) -> Optional[Branch]:
        """Zoomed in: the branch you're on — or, on a line's last branch, the
        one before it, so its neighbours show too. None: no zoom (at the start,
        or still on the first move's own branch, where the board shows)."""
        if not self.zoom or self.tree is None or self.selected is None or self.selected.move is None:
            return None
        branch = next((b for b in self.tree.branches if any(n is self.selected for n in b.nodes)), None)
        if branch is not None and not branch.children and branch.parent is not None:
            branch = branch.parent
        return branch if branch is not None and branch.parent is not None else None

    def set_zoom(self, on: bool) -> None:
        self.zoom = on
        self._relayout()

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
        focus = self.tree.first_move_of(self.selected) if self.selected is not None else None
        opening_tree.layout(self.tree, w, h, focus=focus, zoom=self._zoom_branch())
        self._node_branch = {id(n): (b, i) for b in self.tree.branches for i, n in enumerate(b.nodes)}
        zoomed = self.view_zoom > 1.0
        if self.tree.width > w + 1 and not zoomed:
            self.scroll.pack(fill="x", padx=6, pady=(0, 4))
        else:
            self.scroll.pack_forget()
        if zoomed:
            self._apply_view(w, h)
            self.canvas.configure(scrollregion=(0, 0, w, h))
            self.canvas.xview_moveto(0)
        else:
            self.canvas.configure(scrollregion=(0, 0, self.tree.width, h))
        self.zoom_label.configure(text=f"{self.view_zoom:.1f}×" if zoomed else "")
        self._paint()

    # ---------- free zoom ----------

    MAX_VIEW_ZOOM = 6.0

    def _build_zoom_buttons(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=theme.PANEL_ALT, corner_radius=8)
        style = dict(width=26, height=24, font=theme.FONT_BOLD, corner_radius=6)
        theme.button(bar, "+", lambda: self.zoom_at(None, None, 1.4), **style).pack(side="left", padx=(3, 0), pady=3)
        theme.button(bar, "−", lambda: self.zoom_at(None, None, 1 / 1.4), **style).pack(side="left", padx=(3, 0), pady=3)
        theme.button(bar, "⟲", self.reset_view, **style).pack(side="left", padx=3, pady=3)
        self.zoom_label = theme.label(bar, "", "muted", width=30)
        self.zoom_label.pack(side="left", padx=(0, 4))
        bar.place(relx=1.0, x=-10, y=10, anchor="ne")

    def _apply_view(self, w: float, h: float) -> None:
        """Layout coordinates -> the zoomed view, in place (so clicks and hovering just work)."""
        z = self.view_zoom
        cx, cy = self.view_center or (w / 2, h / 2)
        tx = lambda x: (x - cx) * z + w / 2
        ty = lambda y: (y - cy) * z + h / 2
        stack = [self.tree.root]
        while stack:
            n = stack.pop()
            n.x, n.y = tx(n.x), ty(n.y)
            stack.extend(n.children)
        # zoomed in, the mini board would fill the view: it's hidden (⟲ brings it back)
        self.tree.board = None

    def _layout_point(self, sx: float, sy: float) -> Tuple[float, float]:
        """A point on screen -> the same point in layout coordinates (before the view)."""
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        if self.view_zoom <= 1.0:
            return self.canvas.canvasx(sx), self.canvas.canvasy(sy)
        cx, cy = self.view_center or (w / 2, h / 2)
        return (sx - w / 2) / self.view_zoom + cx, (sy - h / 2) / self.view_zoom + cy

    def zoom_at(self, sx: Optional[float], sy: Optional[float], factor: float) -> None:
        """Zoom by factor, keeping the point under the pointer (or the centre) where it is."""
        if self.tree is None:
            return
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        if sx is None:
            sx, sy = w / 2, h / 2
        lx, ly = self._layout_point(sx, sy)
        z = max(1.0, min(self.MAX_VIEW_ZOOM, self.view_zoom * factor))
        if z <= 1.0001:
            self.reset_view()
            return
        self.view_zoom = z
        self.view_center = (lx - (sx - w / 2) / z, ly - (sy - h / 2) / z)
        self._relayout()

    def reset_view(self) -> None:
        self.view_zoom, self.view_center = 1.0, None
        self._relayout()

    def _on_press(self, event) -> None:
        self._press, self._dragged = (event.x, event.y), False

    def _on_drag(self, event) -> None:
        if self._press is None or self.view_zoom <= 1.0:
            return
        dx, dy = event.x - self._press[0], event.y - self._press[1]
        if not self._dragged and dx * dx + dy * dy < 16:
            return                              # a click, not a drag (yet)
        self._dragged = True
        self._press = (event.x, event.y)
        w, h = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        cx, cy = self.view_center or (w / 2, h / 2)
        self.view_center = (cx - dx / self.view_zoom, cy - dy / self.view_zoom)
        self.canvas.configure(cursor="fleur")
        self._relayout()

    def _on_release(self, event) -> None:
        dragged, self._press = self._dragged, None
        self._dragged = False
        if dragged:
            self.canvas.configure(cursor="")
            return
        self._on_click(event)

    def _on_path(self) -> set:
        """ids of the positions from the root to the selection."""
        ids, node = set(), self.selected
        while node is not None:
            ids.add(id(node))
            node = node.parent
        return ids

    @property
    def overview(self) -> bool:
        """Several first-move trees side by side: keep it plain — no names, move
        labels or start flags (they show once you go into one tree; hovering a
        leaf still names its line)."""
        return (self.tree is not None and self.tree.focus is None
                and sum(1 for b in self.tree.branches if b.parent is None) > 1)

    def _paint(self) -> None:
        c, tree = self.canvas, self.tree
        c.delete("all")
        self._items, self._branch_items = {}, {}
        self._fonts = []                       # label fonts of the last drawing (kept alive while shown)
        if tree is None:
            return
        root = tree.root
        self._paint_board()
        self._paint_zoom_base()
        on_path = self._on_path()
        focus_ids = set()
        if self.focus_path and self.selected is not None and self.selected.move is not None:
            # (at the start nothing is chosen yet: no line to trace ahead)
            node = tree.node_for_path([n.move_uci for n in self.focus_path])
            while node is not None:
                focus_ids.add(id(node))
                node = node.parent

        # bark: thick trunks first, so thinner twigs lie on top
        shown = [b for b in tree.branches if tree.visible(b)]
        for b in sorted(shown, key=lambda b: -b.lines):
            x0, y0 = b.start_xy
            pts = opening_tree.curve(x0, y0, b.end.x, b.end.y)
            item = c.create_line(*pts, smooth="raw", splinesteps=24, width=opening_tree.thickness(b.lines),
                                 fill=BARK, capstyle="round")
            self._branch_items[item] = b

        # glow: the selected path solid, the rest of the focus line dashed
        for b in shown:
            solid = [i for i, node in enumerate(b.nodes) if id(node) in on_path]
            ahead = [i for i, node in enumerate(b.nodes) if id(node) in focus_ids and id(node) not in on_path]
            if solid:
                self._glow_segment(b, 0.0, b.t_at(max(solid)), dash=None)
            if ahead:
                start = b.t_at(max(solid)) if solid else 0.0
                self._glow_segment(b, start, 1.0, dash=(3, 6))

        # beads (every move), knots, leaves
        for b in shown:
            for i, node in enumerate(b.nodes[:-1]):
                if b.is_first_move and i == 0:
                    continue                   # the piece on the mini board is this move
                self._bead(node)
            if not (b.is_first_move and len(b.nodes) == 1):
                self._knot_or_leaf(b)
            elif b.end.line_ends:
                self._knot_or_leaf(b)
        self._label_branches()
        self._paint_start_flag()
        self._paint_deviations()
        self._paint_my_deviations()
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
            if self.overview:
                return
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
        if self.overview:
            return
        on_path = self._on_path()
        for b in self.tree.branches:
            if not self.tree.visible(b) or b.is_first_move or b is self.tree.zoom:
                continue                       # first moves: the pieces on the board; zoomed: in the base line
            # only along your path and the choices leaving it — the moves that matter here
            on = any(id(n) in on_path for n in b.nodes)
            choice = b.parent is not None and any(id(n) in on_path for n in b.parent.nodes)
            if not (on or choice):
                continue
            x0, y0 = b.start_xy
            x, y = opening_tree.bezier(x0, y0, b.end.x, b.end.y, LABEL_AT if len(b.nodes) > 1 else 0.5)
            self.canvas.create_text(x + 8, y, text=move_label(b.nodes[0]), anchor="w", fill=theme.TEXT_MUTED,
                                    font=(theme.FONT_FAMILY, 9))

    def _marker(self) -> None:
        node = self.selected
        if node is None or node.move is None:     # the start: no single spot to mark
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
        return [n for b in self.tree.branches if self.tree.visible(b) for n in b.nodes]

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
        if over and over[0] in self._board_items:
            node = self._board_items[over[0]]
            self._set_hover(node if node.move is not None else None)
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
        if node.line_ends:                     # a leaf: name its line(s)
            text = short_name(node.line_ends[0][0], self.repertoire) + (
                f" (+{len(node.line_ends) - 1})" if len(node.line_ends) > 1 else "")
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
        if over and over[0] in self._my_items and self.on_my_deviation is not None:
            self.on_my_deviation(self._my_items[over[0]])
            return
        if over and over[0] in self._dev_items and self.on_deviation is not None:
            self.on_deviation(*self._dev_items[over[0]])
            return
        if over and over[0] in self._board_items:     # the mini board: a piece, or all trees
            self.on_select(self._board_items[over[0]])
            return
        node = self._node_at(event)
        if node is not None:
            self.on_select(node)

    def _wheel(self, delta: int) -> None:
        if self.on_wheel is not None:
            self.on_wheel(delta)

    # ---------- your own deviations: a red badge where you left your prep ----------

    @staticmethod
    def _star_points(x: float, y: float, outer: float, inner: float) -> List[float]:
        pts = []
        for i in range(10):
            r = outer if i % 2 == 0 else inner
            a = math.radians(-90 + 36 * i)
            pts += [x + r * math.cos(a), y + r * math.sin(a)]
        return pts

    def _paint_my_deviations(self) -> None:
        """Numbered stars from your games: red = you left your prep here, blue = your opponent did."""
        c = self.canvas
        self._my_items = {}
        if self.tree is None or self.overview:
            return
        font = (theme.FONT_FAMILY, 7, "bold")
        for node in self._nodes():
            # yours up-right, theirs up-left: nearby positions' stars don't hide each other
            for devs, color, dx in ((self.my_deviations.get(id(node)), MY_DEVIATION, 10),
                                    (self.opp_deviations.get(id(node)), OPP_DEVIATION, -10)):
                if not devs:
                    continue
                games = sum(d.games for d in devs)
                x, y = node.x + dx, node.y - 10
                outer = 10 if games < 100 else 12
                star = c.create_polygon(*self._star_points(x, y, outer, outer * 0.5), fill=color,
                                        outline=theme.PANEL_BG, width=1.2)
                label = c.create_text(x, y + 1, text=str(games) if games < 1000 else "1k", fill="#FFFFFF", font=font)
                top = max(devs, key=lambda d: d.games)
                for item in (star, label):
                    self._my_items[item] = top
                    c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="hand2"))
        for item in self._my_items:          # stars above the twigs and rings
            c.tag_raise(item)

    # ---------- Lichess deviations: short grey "wild" twigs ----------

    def _paint_deviations(self) -> None:
        c = self.canvas
        self._dev_items = {}
        self.sidelines_hidden = False
        if self.tree is None or self.overview:      # inside a tree only
            return
        by_id = {id(n): n for n in self._nodes()}
        for node_id, devs in self.deviations.items():
            node = by_id.get(node_id)
            if node is None:
                continue
            if node.move is None:
                continue                       # first moves: the board shows those
            inside = self.inside(node)
            if not inside and not self.show_before:
                continue
            line_color, text_color = (WILD, "#9AA1AD") if inside else (WILD_BEFORE, WILD_BEFORE_TEXT)
            if node is not self.selected:
                # elsewhere just a thin ring: "Lichess players leave your lines here" —
                # the twigs (with names, clickable) show for the selected position only
                ring = likely_colors(max(d.share for d in devs))[0] if inside else line_color
                c.create_oval(node.x - 6, node.y - 6, node.x + 6, node.y + 6, outline=ring, width=1.5)
                continue
            self._paint_sideline_list(node, devs, inside, line_color, text_color)

    def _paint_sideline_list(self, node: Node, devs: list, inside: bool, line_color: str,
                             text_color: str) -> None:
        """The selected position's sidelines as a little list beside it, put
        where neither the labels nor their connecting lines touch a branch or
        another label: the first clear spot of a set of candidates (left /
        right, below / above / level, nearer / further out). No clear spot
        (a crowded, un-zoomed tree): not drawn at all — sidelines_hidden tells
        the caller, which lists them in words instead."""
        c = self.canvas
        big = self.tree.zoom is not None          # zoomed in: larger, with a popularity bar
        size, weight, row_h, r = (10, "bold", 22, 5) if big else (9, "normal", 17, 4)
        font = tkfont.Font(family=theme.FONT_FAMILY, size=size, weight=weight)
        texts = [f"{dev.san.split('.')[-1]} {dev.share:.0%}" for dev in devs]
        width = r + 6 + max(font.measure(t) for t in texts)
        height = row_h * len(devs)

        def rows(side: int, dx: float, top: float):
            """(ring x, ring y) for each row of this placement."""
            return [(node.x + side * dx, top + row_h * i + row_h / 2) for i in range(len(devs))]

        def crowding(side: int, dx: float, top: float) -> int:
            x_ring = node.x + side * dx
            x0 = min(x_ring - r - 2, x_ring + side * width)
            x1 = max(x_ring + r + 2, x_ring + side * width)
            if x0 < 2 or x1 > self.tree.width - 2 or top < 2 or top + height > self.tree.height - 2:
                return 10 ** 6                    # off the canvas
            hits = set(c.find_overlapping(x0, top, x1, top + height))
            for rx, ry in rows(side, dx, top):    # the dotted connectors too (not right at the knot)
                for t in (0.35, 0.6, 0.85):
                    px, py = node.x + (rx - node.x) * t, node.y + (ry - node.y) * t
                    hits |= set(c.find_overlapping(px - 2, py - 2, px + 2, py + 2))
            return len(hits)

        candidates = []
        for dx in (36, 52, 72, 96):
            for side in (-1, 1):
                for top in (node.y + 8, node.y - 8 - height, node.y - height / 2):
                    candidates.append((side, dx, top))
        best = next((cand for cand in candidates if crowding(*cand) == 0), None)
        if best is None:
            self.sidelines_hidden = True       # never on top of a branch
            return
        side, dx, top = best
        for (rx, ry), dev, text in zip(rows(side, dx, top), devs, texts):
            # colour by likelihood (other openings, before yours starts, stay faint)
            stroke, ink = likely_colors(dev.share) if inside else (line_color, text_color)
            c.create_line(node.x, node.y, rx, ry, fill=stroke, width=2, dash=(2, 3))
            ring = c.create_oval(rx - r, ry - r, rx + r, ry + r, outline=stroke, width=2, fill=theme.PANEL_BG)
            label = c.create_text(rx + side * (r + 4), ry, text=text, anchor="w" if side > 0 else "e",
                                  fill=ink, font=font)
            items = [ring, label]
            if big:                               # how popular, as a little bar under the name
                x0, _, x1, y1 = c.bbox(label)
                bar_w = max(4, (x1 - x0) * min(1.0, dev.share / 0.5))
                bx = x0 if side > 0 else x1 - bar_w
                items.append(c.create_rectangle(bx, y1 + 1, bx + bar_w, y1 + 3, fill=stroke, outline=""))
            for item in items:
                self._dev_items[item] = (node, dev.uci)
        self._fonts = getattr(self, "_fonts", []) + [font]    # keep it alive while drawn

    def _paint_start_flag(self) -> None:
        """A small amber flag where each opening starts (not at the very start)."""
        if self.overview:
            return
        visible = {id(n) for n in self._nodes()}
        at: Dict[int, List[str]] = {}
        nodes: Dict[int, Node] = {}
        for name, node in self.starts:
            # (an opening that starts with its first move needs no flag: the piece shows it)
            if node is not None and node.move is not None and node.parent.move is not None and id(node) in visible:
                at.setdefault(id(node), []).append(name)
                nodes[id(node)] = node
        c = self.canvas
        for node_id, names in at.items():
            node = nodes[node_id]
            right = node.x < self.tree.width - 120
            fx = node.x + (10 if right else -10)
            c.create_line(fx, node.y - 2, fx, node.y - 22, fill=GLOW, width=2)
            c.create_polygon(fx, node.y - 22, fx + (12 if right else -12), node.y - 18, fx, node.y - 14,
                             fill=GLOW, outline="")
            c.create_text(fx + (16 if right else -16), node.y - 18, text=" / ".join(names) + " starts",
                          anchor="w" if right else "e", fill=GLOW, font=(theme.FONT_FAMILY, 9, "bold"))

    # ---------- the mini board along the bottom ----------

    def _paint_zoom_base(self) -> None:
        tree = self.tree
        if tree.zoom is None:
            return
        start = tree.zoom.nodes[0].parent
        c = self.canvas
        c.create_line(start.x, start.y, start.x, tree.height, width=opening_tree.thickness(tree.zoom.lines),
                      fill=BARK, capstyle="butt")
        c.create_oval(start.x - 7, start.y - 7, start.x + 7, start.y + 7, fill=theme.PANEL_BG,
                      outline=theme.TEXT, width=2.5)
        where = "the start" if start.move is None else move_label(start)
        # one line along the bottom edge (the branch's own move label isn't drawn when zoomed)
        c.create_text(start.x + 12, tree.height - 8, text=f"zoomed in · after {where} · {move_label(tree.zoom.nodes[0])}",
                      anchor="w", fill=theme.TEXT_MUTED, font=(theme.FONT_FAMILY, 9))
        self._items[c.find_all()[-2]] = start            # click the knot to step back out

    def _paint_board(self) -> None:
        """Your pieces as they start; each first move's piece stands on its
        square (a tree grows out of it). Click a piece to go into its tree,
        anywhere else on the board for all of them."""
        c, tree = self.canvas, self.tree
        board = tree.board
        self._board_items = {}
        if board is None:
            return
        firsts = {}                            # destination square -> first-move node
        moved_from = set()
        for top in tree.root.children:
            move = chess.Move.from_uci(top.move.move_uci)
            firsts[move.to_square] = top
            moved_from.add(move.from_square)
        start = chess.Board()
        sq_px = board.square
        for rank in board.ranks:
            for file in range(8):
                sq = chess.square(file, rank)
                x, y = board.center(sq)
                light = (file + rank) % 2 == 1
                fill = theme.BOARD_LIGHT if light else theme.BOARD_DARK
                rect = c.create_rectangle(x - sq_px / 2, y - sq_px / 2, x + sq_px / 2, y + sq_px / 2,
                                          fill=fill, outline="")
                self._board_items[rect] = firsts.get(sq, tree.root)
                piece = start.piece_at(sq) if sq not in moved_from else None
                if sq in firsts:
                    piece = start.piece_at(chess.Move.from_uci(firsts[sq].move.move_uci).from_square)
                if piece is None:
                    continue
                glyph = self._glyph(x, y, piece)
                for item in glyph:
                    self._board_items[item] = firsts.get(sq, tree.root)
        # first moves: amber ring (dimmed when another tree has the focus)
        for sq, node in firsts.items():
            x, y = board.center(sq)
            active = tree.focus is None or tree.focus is node
            ring = c.create_rectangle(x - sq_px / 2 + 1, y - sq_px / 2 + 1, x + sq_px / 2 - 1, y + sq_px / 2 - 1,
                                      outline=GLOW if active else theme.BORDER_STRONG, width=2)
            self._board_items[ring] = node
            if not active:                     # another tree is showing: grey this piece out
                veil = c.create_rectangle(x - sq_px / 2, y - sq_px / 2, x + sq_px / 2, y + sq_px / 2,
                                          fill=theme.PANEL_BG, outline="", stipple="gray50")
                self._board_items[veil] = node

    def _glyph(self, x: float, y: float, piece: chess.Piece) -> List[int]:
        c, size = self.canvas, max(10, int(self.tree.board.square * 0.68))
        font = (theme.FONT_FAMILY, size)
        symbol = GLYPHS[piece.piece_type]
        if piece.color == chess.WHITE:
            fill, outline = theme.PIECE_WHITE_FILL, theme.PIECE_WHITE_OUTLINE
        else:
            fill, outline = theme.PIECE_BLACK_FILL, theme.PIECE_BLACK_OUTLINE
        items = [c.create_text(x + dx, y + dy, text=symbol, font=font, fill=outline)
                 for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        items.append(c.create_text(x, y, text=symbol, font=font, fill=fill))
        return items

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
        if self.view_zoom > 1.0:
            return                              # zoomed: you move the view yourself
        if node is None or self.tree is None or self.tree.width <= self.canvas.winfo_width() + 1:
            return
        left, right = (f * self.tree.width for f in self.canvas.xview())
        if not left + 30 <= node.x <= right - 30:
            self.canvas.xview_moveto(max(0.0, (node.x - (right - left) / 2) / self.tree.width))
