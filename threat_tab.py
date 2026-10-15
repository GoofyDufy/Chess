"""What's the threat? — the second mode of the Tactics & Threats page (rules in
threat.py). Name your opponent's threat (play their move, or "No threat"),
then answer it with your move.

Threading as in spot_tab.py: one Stockfish on a background thread takes jobs
from a queue, answers come back through a second queue drained by after();
the next round is prepared while you play this one.
"""

from __future__ import annotations

import io
import queue
import threading
from typing import Optional

import chess
import chess.engine
import chess.pgn
import customtkinter as ctk

import db
import engine_service
import progression
import spot
import theme
import threat
from board_widget import BOARD_PIXELS, GAME_MOVE_COLOR, REPLY_COLOR, ChessBoardWidget
from eval_bar import EvalBar

THREAT_COLOR = theme.ERROR             # red arrow: their threat
POLL_MS = 100
MAX_TRIES = 30                         # candidates checked before taking either kind
GIVE_UP_TRIES = 80


class ThreatTab(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None, mode_switch=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.on_review_game = on_review_game
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._engine_started = False
        self._pools = {True: [], False: []}          # keyed by "has a threat"
        self._seen = threat.seen_fens(conn)
        self._ready = None                           # (candidate, assessment, previous move)
        self._preparing = False
        self._waiting = False
        self._token = object()
        self.state = "idle"     # idle / loading / naming / judging_threat / answering / judging_answer / result
        self.cand: Optional[threat.Candidate] = None
        self.assessment: Optional[threat.Assessment] = None
        self.board: Optional[chess.Board] = None
        self.previous: Optional[chess.Move] = None
        self.found = False
        self.shown_threat: Optional[threat.Threat] = None
        self.session = {"rounds": 0, "found": 0, "answers": 0, "streak": 0, "best": 0}
        self._build_layout(mode_switch)
        top = self.winfo_toplevel()
        for key, action in (("<KeyPress-n>", self._no_threat), ("<Return>", self._next_round),
                            ("<space>", self._next_round)):
            top.bind(key, lambda e, a=action: self._on_key(a), add="+")
        self.bind("<Destroy>", lambda e: self._jobs.put(None))
        self.bind("<Map>", lambda e: self._on_shown())
        self._refresh_scores()
        self.after(POLL_MS, self._poll)

    # ---------- layout ----------

    def _build_layout(self, mode_switch) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        pad = ctk.CTkFrame(left, fg_color="transparent")
        pad.pack(padx=18, pady=16)
        self.source_label = theme.label(pad, "", "muted", wraplength=BOARD_PIXELS)
        self.source_label.pack(anchor="w", pady=(0, 8))
        board_row = ctk.CTkFrame(pad, fg_color="transparent")
        board_row.pack()
        self.eval_bar = EvalBar(board_row, BOARD_PIXELS)
        self.eval_bar.pack(side="left", padx=(0, 8))
        self.board_widget = ChessBoardWidget(board_row, self._on_move, bg=theme.PANEL_BG)
        self.board_widget.pack(side="left")

        row = ctk.CTkFrame(pad, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        self.none_button = theme.button(row, "No threat  (N)", self._no_threat, width=150)
        self.next_button = theme.button(row, "Next position →", self._next_round, "primary", width=140)
        self.review_button = theme.button(row, "Review game", self._review_game, width=108)

        self.status_label = theme.label(pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(anchor="w", pady=(10, 0))
        self.detail_label = theme.label(pad, "", "body", wraplength=BOARD_PIXELS)
        self.detail_label.pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)
        info = theme.card(right)
        info.pack(fill="x")
        body = ctk.CTkFrame(info, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=16)
        theme.fit_wrap(body)
        if mode_switch:
            mode_switch(body).pack(anchor="w", pady=(0, 10))
        theme.label(body, "What's the threat?", "title").pack(anchor="w")
        theme.label(
            body,
            "Most mistakes start with not noticing what the opponent's last move threatens. "
            "Positions from your own games, right after your opponent moved.\n\n"
            "1. Name the threat: play the move THEY want to make next (you can move their pieces), "
            "or press No threat.\n2. Then answer it with your move.\n\n"
            f"About {round(threat.NO_THREAT_SHARE * 4)} in 4 positions have no real threat. "
            f"+{threat.FOUND_XP} XP for naming the threat (or rightly saying there's none), "
            f"+{threat.ANSWER_XP} XP for a good answer (within {threat.MOVE_OK_CP / 100:.1f} pawns of the best).",
            "muted", wraplength=300,
        ).pack(anchor="w", fill="x", pady=(4, 0))

        scores = theme.card(right)
        scores.pack(fill="x", pady=(16, 0))
        sbody = ctk.CTkFrame(scores, fg_color="transparent")
        sbody.pack(fill="x", padx=18, pady=16)
        theme.fit_wrap(sbody)
        theme.label(sbody, "THIS SESSION", "section").pack(anchor="w")
        self.session_label = theme.label(sbody, "", "stat")
        self.session_label.pack(anchor="w", pady=(2, 0))
        self.session_detail = theme.label(sbody, "", "muted", wraplength=300)
        self.session_detail.pack(anchor="w")
        theme.label(sbody, "ALL TIME", "section").pack(anchor="w", pady=(14, 0))
        self.alltime_label = theme.label(sbody, "", "body", wraplength=300)
        self.alltime_label.pack(anchor="w", pady=(2, 0))

    def _show_buttons(self, *buttons) -> None:
        for b in (self.none_button, self.next_button, self.review_button):
            b.pack_forget()
        for i, b in enumerate(buttons):
            b.pack(side="left", padx=(0 if i == 0 else 8, 0))

    def _on_key(self, action) -> None:
        if not self.winfo_ismapped():
            return
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "Text"):
            return
        action()

    # ---------- rounds ----------

    def _on_shown(self) -> None:
        if self.state == "idle":
            self._next_round()

    def _next_round(self) -> None:
        if self.state not in ("idle", "result", "loading"):
            return
        if self._ready is None:
            self.state = "loading"
            self._waiting = True
            self._show_buttons()
            self.board_widget.clear()
            self.eval_bar.set(None)
            self.source_label.configure(text="")
            self.status_label.configure(text="Finding a position…", text_color=theme.TEXT_MUTED)
            self.detail_label.configure(text="")
            self._prepare()
            return
        self.cand, self.assessment, self.previous = self._ready
        self._ready = None
        self._waiting = False
        self._token = object()
        self.board = chess.Board(self.cand.fen)
        self.found, self.shown_threat = False, None
        self._seen.add(self.cand.fen)
        self.state = "naming"
        me_black = self.board.turn == chess.BLACK
        self.board_widget.show_board(threat.passed(self.board), flipped=me_black)   # their move
        self.board_widget.highlight_move = self.previous
        self.board_widget.redraw()
        self.eval_bar.set(None, flipped=me_black)
        self.source_label.configure(text=self._source_text())
        them = "Black" if me_black is False else "White"
        last = ""
        if self.previous is not None:
            last = f" played {self._san_before(self.previous)}"
        self.status_label.configure(text=f"{them}{last}. What does it threaten?", text_color=theme.TEXT)
        self.detail_label.configure(text=f"Play the move {them} wants to make next — or press No threat.")
        self._show_buttons(self.none_button)
        self._prepare()

    def _san_before(self, move: chess.Move) -> str:
        """SAN of the opponent's last move, from the position before it."""
        row = self.conn.execute("SELECT pgn FROM games WHERE id = ?", (self.cand.game_id,)).fetchone()
        try:
            game = chess.pgn.read_game(io.StringIO(row[0]))
            board = game.board()
            for i, m in enumerate(game.mainline_moves()):
                if i == self.cand.ply - 1:
                    return board.san(m)
                board.push(m)
        except Exception:
            pass
        return move.uci()

    def _source_text(self) -> str:
        game = db.get_game(self.conn, self.cand.game_id)
        if game is None:
            return ""
        return (f"From your game vs {game.opponent_username} · {game.played_at.strftime('%b %d, %Y')} "
                f"· move {self.cand.ply // 2 + 1}")

    def _no_threat(self) -> None:
        if self.state != "naming":
            return
        self._threat_named(None, False, 0)

    def _on_move(self, move: chess.Move) -> None:
        if self.state == "naming":
            shown = threat.passed(self.board)
            shown.push(move)
            self.board_widget.show_board(shown)
            self.state = "judging_threat"
            self.detail_label.configure(text="Checking…")
            self._start_engine()
            self._jobs.put(("threat", self._token, self.cand.fen, self.assessment, move))
        elif self.state == "answering":
            shown = self.board.copy()
            shown.push(move)
            self.board_widget.show_board(shown)
            self.board_widget.set_marked_arrows(self._threat_arrow())
            self.state = "judging_answer"
            self.detail_label.configure(text="Checking your answer…")
            self._start_engine()
            self._jobs.put(("answer", self._token, self.cand.fen, self.assessment, move))

    def _threat_named(self, move: Optional[chess.Move], real: bool, gain: int) -> None:
        """Phase 1 done: say whether you saw it, show the threat, then ask for your answer."""
        a = self.assessment
        if move is None:                                     # "No threat"
            self.found = not a.has_threat
            text = ("✓ Right — no real threat here." if self.found else
                    f"✗ There is one: {threat.describe(self.board, a.threats[0])}.")
            self.shown_threat = a.threats[0] if a.has_threat else None
        elif real:
            self.found = True
            self.shown_threat = threat.Threat(move, gain)
            text = f"✓ Yes — {threat.describe(self.board, self.shown_threat)}."
        else:
            self.found = False
            self.shown_threat = a.threats[0] if a.has_threat else None
            san = threat.passed(self.board).san(move)
            text = (f"✗ {san} isn't much of a threat. The real one: {threat.describe(self.board, a.threats[0])}."
                    if a.has_threat else f"✗ No real threat here — {san} wouldn't gain them much.")
        self.status_label.configure(text=text, text_color=theme.SUCCESS if self.found else theme.ERROR)
        self.detail_label.configure(text=("Now deal with it — your move. Red arrow: their threat."
                                          if self.shown_threat else "Now play a good move."))
        self.state = "answering"
        self.board_widget.show_board(self.board.copy())
        self.board_widget.highlight_move = self.previous
        self.board_widget.set_marked_arrows(self._threat_arrow())
        self._show_buttons()

    def _threat_arrow(self):
        t = self.shown_threat
        return [(t.move.from_square, t.move.to_square, THREAT_COLOR)] if t else []

    def _answered(self, move: chess.Move, good: bool, loss: int) -> None:
        self.state = "result"
        a = self.assessment
        threat.record(self.conn, self.cand.fen, a.has_threat, self.found, good)
        progression.notify()
        s = self.session
        s["rounds"] += 1
        s["found"] += self.found
        s["answers"] += good
        s["streak"] = s["streak"] + 1 if (self.found and good) else 0
        s["best"] = max(s["best"], s["streak"])
        san, best = self.board.san(move), self.board.san(a.best)
        if good and move == a.best:
            verdict = f"✓ {san} — the best answer."
        elif good:
            verdict = f"✓ {san} — a good answer (only {loss / 100:.1f} pawns off {best})."
        else:
            verdict = f"✗ {san} is {loss / 100:.1f} pawns worse than {best}."
        call = "✓ Threat" if self.found else "✗ Threat"
        self.status_label.configure(
            text=f"{call} · {verdict}",
            text_color=theme.SUCCESS if (self.found and good) else theme.WARNING if (self.found or good)
            else theme.ERROR)
        detail = []
        if self.shown_threat:
            detail.append(f"Their threat: {threat.describe(self.board, self.shown_threat)}.")
        played = chess.Move.from_uci(self.cand.played_uci)
        if played in self.board.legal_moves:
            detail.append(f"In your game you played {self.board.san(played)}"
                          + (" — the same." if played == move else
                             " — the mistake this position comes from." if self.cand.expect_threat else "."))
        detail.append("Green: the best answer · red: their threat · blue: your game move.")
        self.detail_label.configure(text="\n".join(detail))
        self.board_widget.show_board(self.board.copy())
        self.board_widget.highlight_move = self.previous
        arrows = self._threat_arrow() + [(a.best.from_square, a.best.to_square, REPLY_COLOR)]
        if played != a.best and played in self.board.legal_moves:
            arrows.append((played.from_square, played.to_square, GAME_MOVE_COLOR))
        self.board_widget.set_marked_arrows(arrows)
        self.eval_bar.set(a.best_cp if self.board.turn == chess.WHITE else -a.best_cp,
                          flipped=self.board_widget.flipped)
        self._show_buttons(self.next_button, self.review_button)
        self._refresh_scores()

    def _review_game(self) -> None:
        if self.cand is not None and self.on_review_game:
            self.on_review_game(self.cand.game_id, self.cand.fen)

    # ---------- preparing rounds ----------

    def _prepare(self, want: Optional[bool] = None, tries: int = 0) -> None:
        if self._ready is not None or (self._preparing and tries == 0):
            return
        if want is None:
            want = threat.want_threat()
        pool = self._pools[want]
        if not pool:
            pool.extend(threat.candidates(self.conn, want, self._seen))
        if not pool:
            other = self._pools[not want] or threat.candidates(self.conn, not want, self._seen)
            if not other:
                self._preparing = False
                self.status_label.configure(
                    text="No positions yet — import and analyze some games first (Import & Analyze).",
                    text_color=theme.TEXT_MUTED)
                return
            self._pools[not want] = other
            want, pool = not want, other
        cand = pool.pop()
        previous, _ = spot.last_move(self.conn, cand)
        self._preparing = True
        self._start_engine()
        self._jobs.put(("assess", cand, previous, want, tries))

    def _on_assessed(self, cand, assessment, previous, want, tries) -> None:
        usable = assessment is not None and previous is not None
        if usable and (assessment.has_threat == want or tries + 1 >= MAX_TRIES):
            self._preparing = False
            self._ready = (cand, assessment, previous)
            if self._waiting:
                self._next_round()
            return
        if tries + 1 >= GIVE_UP_TRIES:
            self._preparing = False
            if self._waiting:
                self.status_label.configure(text="Couldn't find a suitable position — analyze more games, "
                                                 "then try again.", text_color=theme.TEXT_MUTED)
            return
        self._prepare(want, tries + 1)

    # ---------- engine thread ----------

    def _start_engine(self) -> None:
        if self._engine_started:
            return
        if not engine_service.available():
            self.status_label.configure(text="Stockfish not found — see Import & Analyze for setup help.",
                                        text_color=theme.ERROR)
            return
        self._engine_started = True
        threading.Thread(target=self._engine_loop, daemon=True).start()

    def _engine_loop(self) -> None:
        """Jobs from the UI, one at a time, on the app's shared Stockfish (engine_service.py)."""
        while True:
            job = self._jobs.get()
            if job is None:
                break
            try:
                with engine_service.borrow() as engine:
                    if job[0] == "assess":
                        _, cand, previous, want, tries = job
                        board = chess.Board(cand.fen)
                        assessment = None if board.is_game_over() else threat.assess(engine, board)
                        self._results.put(("assessed", cand, assessment, previous, want, tries))
                    elif job[0] == "threat":
                        _, token, fen, assessment, move = job
                        real, gain = threat.judge_threat(engine, chess.Board(fen), assessment, move)
                        self._results.put(("threat", token, move, real, gain))
                    else:
                        _, token, fen, assessment, move = job
                        good, loss = threat.judge_answer(engine, chess.Board(fen), assessment, move)
                        self._results.put(("answer", token, move, good, loss))
            except Exception as e:
                self._results.put(("error", str(e)))

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._results.get_nowait()
                if item[0] == "assessed":
                    self._on_assessed(*item[1:])
                elif item[0] == "threat":
                    _, token, move, real, gain = item
                    if token is self._token and self.state == "judging_threat":
                        self._threat_named(move, real, gain)
                elif item[0] == "answer":
                    _, token, move, good, loss = item
                    if token is self._token and self.state == "judging_answer":
                        self._answered(move, good, loss)
                else:
                    self.status_label.configure(text=f"Engine error: {item[1]}", text_color=theme.ERROR)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    # ---------- scores ----------

    def _refresh_scores(self) -> None:
        s = self.session
        if s["rounds"]:
            self.session_label.configure(text=f"Streak {s['streak']}  ·  best {s['best']}")
            self.session_detail.configure(
                text=f"Threats named {s['found']}/{s['rounds']} · good answers {s['answers']}/{s['rounds']}")
        else:
            self.session_label.configure(text="Streak 0")
            self.session_detail.configure(text="A round counts toward your streak when you name the threat "
                                               "and answer it well.")
        a = threat.summary(self.conn)
        if not a.rounds:
            self.alltime_label.configure(text="No rounds played yet.")
            return
        pct = lambda n, d: f"{100 * n / d:.0f}%" if d else "–"
        self.alltime_label.configure(text=(
            f"Threats spotted: {pct(a.found, a.threats)} ({a.found}/{a.threats})\n"
            f"Calm positions called right: {pct(a.calm_right, a.calm)} ({a.calm_right}/{a.calm})\n"
            f"Good answers: {pct(a.answers_ok, a.rounds)} ({a.answers_ok}/{a.rounds})"))
