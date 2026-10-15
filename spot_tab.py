"""Spot the Tactic tab: a position from your games, the question "is there a
tactic here?", then your move — scored, with streaks (rules in spot.py).

Threading follows the app-wide rule (see puzzle_tab.py): one Stockfish
lives on a background thread taking jobs from a queue; results come back
through a second queue drained by an after() poller. Database reads and
all Tk calls stay on the main thread. The next round is prepared while you
play the current one, so there's no wait between positions.
"""

from __future__ import annotations

import queue
import threading
from typing import List, Optional

import chess
import chess.engine
import customtkinter as ctk

import db
import engine_service
import progression
import spot
import theme
from board_widget import BOARD_PIXELS, GAME_MOVE_COLOR, REPLY_COLOR, ChessBoardWidget
from eval_bar import EvalBar

# candidates to classify before accepting a round of the other kind: real (forcing)
# tactics are ~1 in 7 of the likely-tactic positions (sometimes 20-30 checks, at
# ~0.15 s each, in the background), quiet positions are easy to find
MAX_TRIES = {False: 60, True: 15}      # keyed by "wanted quiet"
GIVE_UP_TRIES = 120                    # ...then give up altogether (too few analyzed games)
POLL_MS = 100
REPLY_DELAY_MS = 600   # pause before the opponent's reply in a multi-move tactic


def _sans(board: chess.Board, moves: List[chess.Move]) -> List[str]:
    b, out = board.copy(), []
    for move in moves:
        out.append(b.san(move))
        b.push(move)
    return out


def _numbered(sans: List[str], start: chess.Board) -> str:
    """'24. Qh4 g6 25. Qxe4' — numbered from the position's real move number."""
    parts, number, white = [], start.fullmove_number, start.turn == chess.WHITE
    for i, san in enumerate(sans):
        if white:
            parts.append(f"{number}. {san}")
        else:
            parts.append(f"{number}... {san}" if i == 0 else san)
            number += 1
        white = not white
    return " ".join(parts)


class SpotTab(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None, mode_switch=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.on_review_game = on_review_game
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._engine_started = False
        # pools of untried candidates: {True: quiet, False: tactical}
        self._pools = {True: [], False: []}
        self._seen = spot.seen_fens(conn)
        self._ready = None             # (candidate, verdict) prepared for the next round
        self._preparing = False
        self._waiting = False          # the board is empty, waiting for _ready
        self._token = object()         # current round; stale engine answers are dropped
        self.state = "idle"            # idle / loading / question / move / judging / result
        self.cand: Optional[spot.Candidate] = None
        self.verdict: Optional[spot.Verdict] = None
        self.board: Optional[chess.Board] = None
        self.said_tactic: Optional[bool] = None
        self.line: List[chess.Move] = []      # correct line to step through after your move
        self.line_index: Optional[int] = None  # None = showing your move; else moves of the line shown
        self.your_move: Optional[chess.Move] = None
        self.session = {"rounds": 0, "calls": 0, "moves": 0, "streak": 0, "best": 0}

        self._build_layout(mode_switch)
        top = self.winfo_toplevel()
        for key, action in (("y", lambda: self._answer(True)), ("n", lambda: self._answer(False)),
                            ("<Return>", self._next_round), ("<space>", self._next_round),
                            ("<Left>", lambda: self._step(-1)), ("<Right>", lambda: self._step(+1))):
            top.bind(key if key.startswith("<") else f"<KeyPress-{key}>",
                     lambda e, a=action: self._on_key(a), add="+")
        # (CTkFrame.bind hooks its inner canvas, so e.widget is never self — no check)
        self.bind("<Destroy>", lambda e: self._jobs.put(None))      # stop the engine thread
        self.bind("<Map>", lambda e: self._on_shown())              # first visit starts a round
        self._refresh_scores()
        self.after(POLL_MS, self._poll)

    # ---------- layout ----------

    def _build_layout(self, mode_switch=None) -> None:
        left = theme.card(self)
        left.pack(side="left", padx=(24, 12), pady=24, anchor="n")
        pad = ctk.CTkFrame(left, fg_color="transparent")
        pad.pack(padx=18, pady=16)

        self.source_label = theme.label(pad, "", "muted", wraplength=BOARD_PIXELS)
        self.source_label.pack(anchor="w", pady=(0, 8))
        board_row = ctk.CTkFrame(pad, fg_color="transparent")
        board_row.pack()
        # evaluation bar: blank (grey) until the final result, so it can't give the answer away
        self.eval_bar = EvalBar(board_row, BOARD_PIXELS)
        self.eval_bar.pack(side="left", padx=(0, 8))
        self.board_widget = ChessBoardWidget(board_row, self._on_move, bg=theme.PANEL_BG)
        self.board_widget.pack(side="left")

        # buttons right under the board (text below can't push them off a short screen)
        row = ctk.CTkFrame(pad, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        self.yes_button = theme.button(row, "Yes — a tactic  (Y)", lambda: self._answer(True),
                                       "primary", width=170)
        self.no_button = theme.button(row, "No — nothing  (N)", lambda: self._answer(False), width=170)
        self.retry_button = theme.button(row, "Try again", self._try_again, "primary", width=120)
        self.reveal_button = theme.button(row, "Show answer", self._reveal, width=130)
        self.back_button = theme.button(row, "◀", lambda: self._step(-1), width=36)
        self.fwd_button = theme.button(row, "▶", lambda: self._step(+1), width=36)
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
        if mode_switch:                 # Is there a tactic? / What's the threat? (gui.TrainerPage)
            mode_switch(body).pack(anchor="w", pady=(0, 10))
        theme.label(body, "Spot the Tactic", "title").pack(anchor="w")
        theme.label(
            body,
            "Positions from your own games. First decide: is there a tactic? Then play your move.\n\n"
            "A tactic means you can force mate, win material by force, or only one move works. "
            f"About {round(spot.QUIET_SHARE * 3)} in 3 positions are quiet — any sensible move is "
            f"fine there, so don't burn time hunting for something that isn't there.\n\n"
            f"Scoring: +{spot.CALL_XP} XP for the right answer, +{spot.MOVE_XP} XP for a good move — "
            f"in a tactic position only the best move counts; in a quiet one any move within "
            f"{spot.MOVE_OK_CP / 100:.1f} pawns of the best.",
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
        for b in (self.yes_button, self.no_button, self.retry_button, self.reveal_button,
                  self.back_button, self.fwd_button, self.next_button, self.review_button):
            b.pack_forget()
        for i, b in enumerate(buttons):
            gap = 0 if i == 0 else 6 if b is self.fwd_button else 8
            b.pack(side="left", padx=(gap, 0))

    # ---------- rounds ----------

    def _on_shown(self) -> None:
        if self.state == "idle":
            self._next_round()

    def _on_key(self, action) -> None:
        if not self.winfo_ismapped():
            return
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "Text"):
            return
        action()

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
        self.cand, self.verdict = self._ready
        self._ready = None
        self._waiting = False
        self._token = object()
        self.board = chess.Board(self.cand.fen)
        self.said_tactic = None
        self.line, self.line_index, self.your_move = [], None, None
        self.line_evals: List[int] = []        # White's view, per position along the line
        self.tries = 0
        self.missed_any = False             # any miss in the round: only a clean run is scored
        self.ply = 0                        # index in verdict.solution of your next move
        self.cur = self.board.copy()        # the position as the round goes on
        self.state = "question"
        self._seen.add(self.cand.fen)
        self.board_widget.load_fen(self.cand.fen, flipped=self.board.turn == chess.BLACK)
        self.eval_bar.set(None, flipped=self.board_widget.flipped)
        self.source_label.configure(text=self._source_text())
        side = "White" if self.board.turn == chess.WHITE else "Black"
        self.status_label.configure(text=f"{side} to move. Is there a tactic?", text_color=theme.TEXT)
        self.detail_label.configure(text="")
        self._show_buttons(self.yes_button, self.no_button)
        self._prepare()            # get the next one ready while you think

    def _source_text(self) -> str:
        game = db.get_game(self.conn, self.cand.game_id)
        if game is None:
            return ""
        move = self.cand.ply // 2 + 1
        return (f"From your game vs {game.opponent_username} · {game.played_at.strftime('%b %d, %Y')} "
                f"· move {move}")

    def _answer(self, said_tactic: bool) -> None:
        if self.state != "question":
            return
        self.said_tactic = said_tactic
        right = said_tactic == self.verdict.is_tactic
        if right and said_tactic:
            text = "✓ Right — there's a tactic. Find it!"
        elif right:
            text = "✓ Right — nothing special here. Play a good, sensible move."
        elif said_tactic:
            text = "✗ Not this time — nothing special here. Play a good, sensible move."
        else:
            text = "✗ There IS a tactic here — you'd have missed it. Try to find it!"
        self.status_label.configure(text=text, text_color=theme.SUCCESS if right else theme.ERROR)
        self.detail_label.configure(text="Make your move on the board.")
        self._show_buttons()
        self.state = "move"

    def _on_move(self, move: chess.Move) -> None:
        """Quiet positions: one move, judged by Stockfish (within half a pawn).
        Tactics: the whole forcing line, move by move — the first move is
        judged by Stockfish (for how far off a miss is), later ones against
        the line; the opponent's replies are played for you."""
        if self.state != "move":
            return
        before = self.cur.copy()
        shown = before.copy()
        shown.push(move)
        self.board_widget.show_board(shown)
        self._last_try = (before, move, None)
        if self.ply == 0:
            self.state = "judging"
            self.detail_label.configure(text="Checking your move…")
            self._start_engine()
            self._jobs.put(("judge", self._token, before.fen(), self.verdict, move))
            return
        expected = self.verdict.solution[self.ply]
        self._after_try(before, move, move == expected or shown.is_checkmate(), None)

    def _on_judged(self, move: chess.Move, good: bool, loss: int) -> None:
        self._after_try(self.cur.copy(), move, good, loss)

    def _after_try(self, before: chess.Board, move: chess.Move, good: bool, loss: Optional[int]) -> None:
        self.tries += 1
        self._last_try = (before, move, loss)
        san = before.san(move)
        if not good:
            # missed: say so, but reveal nothing yet — Try again or Show answer
            self.missed_any = True
            self.state = "missed"
            if not self.verdict.is_tactic:
                text = f"✗ {san} is {loss / 100:.1f} pawns worse than the best move — look for something safer."
            elif self.ply == 0:
                text = f"✗ {san} isn't the tactic." + (
                    " Close in score, but there's something better." if loss is not None and loss <= spot.MOVE_OK_CP else "")
            else:
                text = f"✗ {san} isn't the move — the tactic continues differently."
            self.status_label.configure(text=text, text_color=theme.ERROR)
            self.detail_label.configure(text="Try again (only a clean first run is scored), or Show answer.")
            self._show_buttons(self.retry_button, self.reveal_button)
            return
        self.cur.push(move)
        finished = (not self.verdict.is_tactic or self.cur.is_checkmate()
                    or self.ply + 1 >= len(self.verdict.solution))
        if finished:
            self._show_result(move, True, loss or 0)
            return
        # right, and the line goes on: the opponent's reply, then your next move
        self.state = "reply"
        self.status_label.configure(text=f"✓ {san} — keep going.", text_color=theme.SUCCESS)
        self.detail_label.configure(text="")
        token = self._token
        self.after(REPLY_DELAY_MS, lambda: self._play_reply(token))

    def _play_reply(self, token) -> None:
        if token is not self._token or self.state != "reply":
            return
        reply = self.verdict.solution[self.ply + 1]
        san = self.cur.san(reply)
        self.cur.push(reply)
        self.ply += 2
        self.board_widget.show_board(self.cur.copy())
        self.state = "move"
        self.status_label.configure(text=f"Opponent played {san}. Your move.", text_color=theme.TEXT)
        self.detail_label.configure(text="")

    def _try_again(self) -> None:
        if self.state != "missed":
            return
        self.state = "move"
        self.board_widget.show_board(self.cur.copy())
        side = "White" if self.cur.turn == chess.WHITE else "Black"
        self.status_label.configure(text=f"Try again — {side} to move.", text_color=theme.TEXT)
        self.detail_label.configure(text="Make your move on the board.")
        self._show_buttons()

    def _reveal(self) -> None:
        if self.state != "missed":
            return
        before, move, loss = self._last_try
        self._show_result(move, False, loss, before)

    def _show_result(self, move: chess.Move, good: bool, loss: Optional[int],
                     before: Optional[chess.Board] = None) -> None:
        """good: the latest move was right. Scored: a clean run (no misses)."""
        self.state = "result"
        call_right = self.said_tactic == self.verdict.is_tactic
        scored_good = not self.missed_any
        spot.record(self.conn, self.cand.fen, self.verdict, self.said_tactic, scored_good)
        progression.notify()
        s = self.session
        s["rounds"] += 1
        s["calls"] += call_right
        s["moves"] += scored_good
        s["streak"] = s["streak"] + 1 if (call_right and scored_good) else 0
        s["best"] = max(s["best"], s["streak"])

        multi = self.verdict.is_tactic and self.verdict.my_moves > 1
        line_text = _numbered(_sans(self.board, self.verdict.solution), self.board)
        if multi:
            n = self.verdict.my_moves
            if good and scored_good:
                verdict_move = f"✓ Solved — all {n} moves right."
            elif good:
                verdict_move = f"✓ Solved, with retries (only a clean run scores the moves)."
            else:
                verdict_move = f"✗ Not solved. The line: {line_text}."
        else:
            san = (before or self.board).san(move)
            loss = loss or 0
            if good and not scored_good:
                verdict_move = f"✓ {san} — found it on try {self.tries} (only the first try scores the move)."
            elif good and move == self.verdict.best:
                verdict_move = f"✓ {san} — the best move."
            elif good:
                verdict_move = f"✓ {san} — a good move (only {loss / 100:.1f} pawns off the best)."
            elif self.verdict.is_tactic and loss <= spot.MOVE_OK_CP:
                # close in score, but not the tactic — only the best move counts here
                verdict_move = (f"✗ {san} is decent ({loss / 100:.1f} pawns off), but not the tactic: "
                                f"{self.board.san(self.verdict.best)}.")
            else:
                verdict_move = f"✗ {san} is {loss / 100:.1f} pawns worse than the best move."
        self.status_label.configure(
            text=f"{'✓' if call_right else '✗'} Your call · {verdict_move}",
            text_color=theme.SUCCESS if (call_right and scored_good) else theme.WARNING if (call_right or good)
            else theme.ERROR)
        best = self.verdict.best
        detail = self.verdict.describe(self.board)
        if multi:
            detail += f"\nThe whole line: {line_text}."
        self.detail_label.configure(text=detail + "\nGreen arrow: the best move. "
                                    "Blue arrow: what you played in the game.")
        if multi:
            self.board_widget.show_board(self.board.copy())     # back to the start, where the arrows point
        arrows = [(best.from_square, best.to_square, REPLY_COLOR)]
        played = chess.Move.from_uci(self.cand.played_uci)
        if played != best and played in self.board.legal_moves:
            arrows.append((played.from_square, played.to_square, GAME_MOVE_COLOR))
        self._result_arrows = arrows
        self.board_widget.set_marked_arrows(arrows)
        self._set_bar(self.verdict.best_cp if multi else self.verdict.best_cp - (loss or 0))
        self.your_move = move
        self.line = list(self.verdict.pv) or [best]    # replaced by a deeper line in a moment
        self.line_index = None
        self._show_buttons(self.back_button, self.fwd_button, self.next_button, self.review_button)
        self._update_step_buttons()
        self._jobs.put(("line", self._token, self.cand.fen, self.verdict))
        self._refresh_scores()

    # ---------- walking through the correct line ----------

    def _step(self, delta: int) -> None:
        """◀ / ▶ (or ← / →) after your move: from your move, ◀ goes to the start
        position and ▶ to the best move; then along the engine's line."""
        if self.state != "result" or not self.line:
            return
        if self.line_index is None:
            index = 0 if delta < 0 else 1
        else:
            index = max(0, min(len(self.line), self.line_index + delta))
        self.line_index = index
        board = self.board.copy()
        sans = []
        for move in self.line[:index]:
            sans.append(board.san(move))
            board.push(move)
        self.board_widget.show_board(board)
        self._update_step_buttons()
        if index < len(self.line_evals):
            self.eval_bar.set(self.line_evals[index])
        else:
            self._set_bar(self.verdict.best_cp)       # until the line's evaluations arrive
        start = self.board
        text = _numbered(sans, start) if sans else "the starting position"
        more = " …" if index < len(self.line) else ""
        self.status_label.configure(text=f"Correct line ({index}/{len(self.line)}): {text}{more}",
                                    text_color=theme.TEXT)

    def _set_bar(self, cp_me: int) -> None:
        """Evaluation bar from the puzzle side's point of view."""
        self.eval_bar.set(cp_me if self.board.turn == chess.WHITE else -cp_me)

    def _update_step_buttons(self) -> None:
        at = self.line_index
        self.back_button.configure(state="normal" if at is None or at > 0 else "disabled")
        self.fwd_button.configure(state="normal" if at is None or at < len(self.line) else "disabled")

    def _review_game(self) -> None:
        if self.cand is not None and self.on_review_game:
            self.on_review_game(self.cand.game_id, self.cand.fen)

    # ---------- preparing rounds ----------

    def _prepare(self, quiet: Optional[bool] = None, tries: int = 0) -> None:
        """Classify candidates in the background until one of the wanted kind
        (quiet / tactic, about 1 in 3 quiet) is ready."""
        if self._ready is not None or (self._preparing and tries == 0):
            return
        if quiet is None:
            quiet = spot.want_quiet()
        pool = self._pools[quiet]
        if not pool:
            pool.extend(spot.candidates(self.conn, quiet, self._seen))
        if not pool:
            other = self._pools[not quiet] or spot.candidates(self.conn, not quiet, self._seen)
            if not other:
                self._preparing = False
                self.status_label.configure(
                    text="No positions yet — import and analyze some games first (Import & Analyze).",
                    text_color=theme.TEXT_MUTED)
                return
            self._pools[not quiet] = other
            quiet, pool = not quiet, other
        cand = pool.pop()
        previous, captured = spot.last_move(self.conn, cand)
        self._preparing = True
        self._start_engine()
        self._jobs.put(("classify", cand, previous, captured, quiet, tries))

    def _on_classified(self, cand, verdict, trivial, quiet, tries) -> None:
        usable = (verdict is not None and not trivial and verdict.reason != "unclear"
                  and (verdict.is_tactic or spot.usable_as_quiet(cand)))   # quiet: move 6 onward
        # take it if it's the wanted kind — or, after MAX_TRIES misses, any usable position
        if usable and (verdict.is_tactic != quiet or tries + 1 >= MAX_TRIES[quiet]):
            self._preparing = False
            self._ready = (cand, verdict)
            if self._waiting:
                self._next_round()
            return
        if tries + 1 >= GIVE_UP_TRIES:
            self._preparing = False
            if self._waiting:
                self.status_label.configure(text="Couldn't find a suitable position — analyze more games "
                                                 "(Import & Analyze), then try again.", text_color=theme.TEXT_MUTED)
            return
        self._prepare(quiet, tries + 1)

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
                    if job[0] == "classify":
                        _, cand, previous, captured, quiet, tries = job
                        board = chess.Board(cand.fen)
                        verdict, trivial = None, True
                        if not board.is_game_over():
                            verdict = spot.classify(engine, board, captured)
                            trivial = spot.is_trivial(board, verdict.best, previous, captured)
                        self._results.put(("classified", cand, verdict, trivial, quiet, tries))
                    elif job[0] == "line":
                        _, token, fen, verdict = job
                        line = spot.correct_line(engine, chess.Board(fen), verdict)
                        self._results.put(("line", token, line, None))
                        self._results.put(("line", token, line, spot.line_evals(engine, chess.Board(fen), line)))
                    else:
                        _, token, fen, verdict, move = job
                        good, loss = spot.judge_move(engine, chess.Board(fen), verdict, move)
                        self._results.put(("judged", token, move, good, loss))
            except Exception as e:
                self._results.put(("error", str(e)))

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._results.get_nowait()
                if item[0] == "classified":
                    self._on_classified(*item[1:])
                elif item[0] == "line":
                    _, token, line, evals = item
                    if token is self._token and self.state == "result":
                        self.line = line          # the deeper search's line (and its evaluations)
                        if self.line_index is not None and self.line_index > len(line):
                            self.line_index = len(line)
                        if evals is not None:
                            self.line_evals = evals
                            if self.line_index is not None:       # already stepping: update the bar
                                self.eval_bar.set(evals[min(self.line_index, len(evals) - 1)])
                        self._update_step_buttons()
                elif item[0] == "judged":
                    _, token, move, good, loss = item
                    if token is self._token and self.state == "judging":
                        self._on_judged(move, good, loss)
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
                text=f"Right calls {s['calls']}/{s['rounds']} · good moves {s['moves']}/{s['rounds']}")
        else:
            self.session_label.configure(text="Streak 0")
            self.session_detail.configure(text="A round counts toward your streak when both your call "
                                               "and your move are right.")
        a = spot.summary(self.conn)
        if not a.rounds:
            self.alltime_label.configure(text="No rounds played yet.")
            return
        pct = lambda n, d: f"{100 * n / d:.0f}%" if d else "–"
        self.alltime_label.configure(text=(
            f"Tactics spotted: {pct(a.spotted, a.tactics)} ({a.spotted}/{a.tactics})\n"
            f"False alarms on quiet positions: {pct(a.false_alarms, a.quiet)} ({a.false_alarms}/{a.quiet})\n"
            f"Good moves: {pct(a.moves_ok, a.rounds)} ({a.moves_ok}/{a.rounds})"))
