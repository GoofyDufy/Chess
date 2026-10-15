"""Sacrifice!! — the third mode of the Tactics & Threats page (rules in
sac.py): play the sacrifice, then find each follow-up against Stockfish's
best defence until the position calms down, you're clearly winning, or mate.

Threading as in spot_tab.py / threat_tab.py: one Stockfish on a background
thread takes jobs from a queue; answers come back through a second queue
drained by after(). Finding your own amazing moves reads the database on
its own read-only connection (also a background thread); everything else
touching the database or Tk stays on the main thread.
"""

from __future__ import annotations

import io
import queue
import sqlite3
import threading
from typing import List, Optional

import chess
import chess.engine
import chess.pgn
import customtkinter as ctk

import amazing
import db
import engine_service
import explorer
import lichess_puzzles
import my_lines
import progression
import sac
import theme
from analyzer import win_chances
from analysis_board import Analysis, IdeasPanel
from board_widget import BOARD_PIXELS, REPLY_COLOR, ChessBoardWidget
from eval_bar import EvalBar

SAC_COLOR = "#F472B6"          # the sacrifice (the !! colour)
POLL_MS = 100
REPLY_DELAY_MS = 500
MAX_PREP_TRIES = 30            # candidates checked for a real follow-up before giving up
LICHESS_BATCH = 100


def _numbered(board: chess.Board, moves: List[chess.Move]) -> str:
    b, parts = board.copy(), []
    for i, m in enumerate(moves):
        n = b.fullmove_number
        parts.append(f"{n}. {b.san(m)}" if b.turn == chess.WHITE else (f"{n}... {b.san(m)}" if i == 0 else b.san(m)))
        b.push(m)
    return " ".join(parts)


class SacTab(ctk.CTkFrame):
    def __init__(self, master, conn, on_review_game=None, mode_switch=None):
        super().__init__(master, fg_color="transparent")
        self.conn = conn
        self.on_review_game = on_review_game
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._engine_started = False
        self._pool: Optional[List[sac.Candidate]] = None     # None: still finding your amazing moves
        self._ready: Optional[sac.Candidate] = None
        self._preparing = False
        self._prep_tries = 0
        self._waiting = False
        self._token = object()
        self.state = "idle"     # idle / loading / sac / thinking / move / judging / missed / result
        self.cand: Optional[sac.Candidate] = None
        self.start: Optional[chess.Board] = None
        self.board: Optional[chess.Board] = None
        self.me = chess.WHITE
        self.step: Optional[sac.Step] = None
        self.moves_done = 0
        self.misses = 0
        self.line: List[chess.Move] = []
        self.session = {"rounds": 0, "clean": 0, "streak": 0, "best": 0}
        # analysis after a round (analysis_board.py): its own board from the final position
        self._result_texts = ("", "")
        self._build(mode_switch)
        self.bind("<Destroy>", lambda e: self._jobs.put(None), add="+")
        self.bind("<Map>", lambda e: self._on_shown())
        self._refresh_scores()
        self.after(POLL_MS, self._poll)

    # ---------- layout ----------

    def _build(self, mode_switch) -> None:
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
        self.retry_button = theme.button(row, "Try again", self._try_again, "primary", width=110)
        self.show_button = theme.button(row, "Show the move", self._show_move, width=150)
        self.next_button = theme.button(row, "Next sacrifice →", self._next_round, "primary", width=150)
        self.review_button = theme.button(row, "Review game", self._review_game, width=108)
        self.analyze_button = theme.button(row, "Analyze", self._start_analysis, width=84)
        self.back_step_button = theme.button(row, "◀", lambda: self.analysis.step(-1), width=36)
        self.fwd_step_button = theme.button(row, "▶", lambda: self.analysis.step(+1), width=36)
        self.done_button = theme.button(row, "Back", self._stop_analysis, width=80)
        self.game_line_button = theme.button(row, "Your game", self._analyse_game_line, width=100)
        self._game_line: List[chess.Move] = []     # what you actually played from the sacrifice on
        self._button_row = row
        self.status_label = theme.label(pad, "", "status", wraplength=BOARD_PIXELS)
        self.status_label.pack(anchor="w", pady=(10, 0))
        self.detail_label = theme.label(pad, "", "body", wraplength=BOARD_PIXELS)
        self.detail_label.pack(anchor="w", pady=(2, 0))
        # analysis: Stockfish's top lines, a row each, its dot in its arrow's colour
        self.ideas_frame = IdeasPanel(pad, lambda i: self.analysis.follow(i))
        self.analysis = Analysis(self, self.board_widget, self.ideas_frame, self.eval_bar,
                                 flipped=lambda: self.me == chess.BLACK, title=self._analysis_title,
                                 hint="click a line, then ▶ · ◀ takes back")

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(12, 24), pady=24)
        info = theme.card(right)
        info.pack(fill="x")
        body = ctk.CTkFrame(info, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=16)
        theme.fit_wrap(body)
        if mode_switch:
            mode_switch(body).pack(anchor="w", pady=(0, 10))
        theme.label(body, "Sacrifice!!", "title").pack(anchor="w")
        theme.label(
            body,
            "A sacrifice is only amazing if you follow it up. Find the sacrifice, then find each next move "
            "against Stockfish's best defence — until it's mate, you're clearly winning, or the position "
            "calms down (several good moves instead of one). A wrong try gets a hint (which piece).\n\n"
            "Sacrifices from your own games (your !! amazing moves) and Lichess sacrifice puzzles. "
            f"+{sac.CLEAN_XP} XP for a clean line, +{sac.DONE_XP} if you needed help.",
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
        self.pool_label = theme.label(sbody, "", "muted", wraplength=300)
        self.pool_label.pack(anchor="w", pady=(10, 0))
        self.lichess_button = theme.button(sbody, f"+ {LICHESS_BATCH} Lichess sacrifice puzzles",
                                           self._add_lichess, height=30)
        self.lichess_button.pack(anchor="w", pady=(6, 0))

    def _show_buttons(self, *buttons) -> None:
        for b in (self.retry_button, self.show_button, self.next_button, self.review_button, self.analyze_button,
                  self.back_step_button, self.fwd_step_button, self.game_line_button, self.done_button):
            b.pack_forget()
        for i, b in enumerate(buttons):
            b.pack(side="left", padx=(0 if i == 0 else 8, 0))

    # ---------- positions ----------

    def _on_shown(self) -> None:
        if self.state != "idle":
            return
        self.state = "loading"
        self._waiting = True
        self.status_label.configure(text="Finding your sacrifices…", text_color=theme.TEXT_MUTED)
        self._load_pool()

    def _load_pool(self) -> None:
        """Your amazing moves (own connection, background) + Lichess sacrifice puzzles."""
        path = my_lines.db_path(self.conn)

        def worker():
            conn = sqlite3.connect(path, timeout=30)          # its own connection (saves what it finds)
            try:
                found, missed = amazing.find_all(conn), amazing.find_missed(conn)
            except Exception:
                found, missed = [], []
            finally:
                conn.close()
            self._results.put(("pool", (found, missed)))

        threading.Thread(target=worker, daemon=True).start()

    def _set_pool(self, found_missed) -> None:
        found, missed_found = found_missed
        own, missed = sac.own_candidates(found), sac.missed_candidates(missed_found)
        theirs = sac.lichess_candidates(self.conn)
        names = {r["id"]: r for r in self.conn.execute("SELECT id, opponent_username, played_at FROM games")}
        for c in own + missed:
            g = names.get(c.game_id)
            if g is not None:
                c.label = (f"From your game vs {g['opponent_username']} · {g['played_at'][:10]}"
                           + (" · one you missed" if c.source == "missed" else ""))
        self._pool = sac.pick(own + missed + theirs, sac.seen(self.conn))
        self.pool_label.configure(text=f"{len(own)} sacrifices you played · {len(missed)} you missed · "
                                       f"{len(theirs)} Lichess sacrifice puzzles (rounds with nothing to find "
                                       "afterwards are skipped).")
        self._prepare()

    def _prepare(self) -> None:
        if self._ready is not None or self._preparing or self._pool is None:
            return
        if not self._pool or self._prep_tries >= MAX_PREP_TRIES:
            if self._waiting:
                self.status_label.configure(
                    text="No sacrifices to play yet — analyze more games, or add Lichess sacrifice puzzles (right).",
                    text_color=theme.TEXT_MUTED)
            return
        cand = self._pool.pop(0)
        self._preparing = True
        self._prep_tries += 1
        self._start_engine()
        self._jobs.put(("prepare", cand))

    def _on_prepared(self, cand: sac.Candidate, follow_ups: int) -> None:
        self._preparing = False
        if follow_ups >= 1:
            self._ready = cand
            self._prep_tries = 0
            if self._waiting:
                self._next_round()
        else:
            self._prepare()                    # nothing to find after this one: the next

    # ---------- a round ----------

    def _next_round(self) -> None:
        if self.state == "analysis":
            self._stop_analysis()
        if self.state not in ("idle", "loading", "result"):
            return
        if self._ready is None:
            self.state = "loading"
            self._waiting = True
            self._show_buttons()
            self.board_widget.clear()
            self.status_label.configure(text="Finding a sacrifice with a follow-up…", text_color=theme.TEXT_MUTED)
            self.detail_label.configure(text="")
            self._prepare()
            return
        self.cand, self._ready, self._waiting = self._ready, None, False
        self._token = object()
        self.start = chess.Board(self.cand.fen)
        self.board = self.start.copy()
        self.me = self.board.turn
        self.moves_done, self.misses, self.line, self.step = 0, 0, [], None
        self._missed_here = False                # one miss counted per follow-up at most
        self.state = "sac"
        self.board_widget.show_board(self.board.copy(), flipped=self.me == chess.BLACK)   # the sacrifice: yours to find
        self.eval_bar.set(None, flipped=self.me == chess.BLACK)
        self.source_label.configure(text=self.cand.label)
        side = "White" if self.me == chess.WHITE else "Black"
        self.status_label.configure(text=f"{side} to move — find the sacrifice!!", text_color=SAC_COLOR)
        self.detail_label.configure(text="A piece sacrifice works here. Find it, then find each follow-up "
                                         "against the best defence.")
        self._show_buttons()
        self._prepare()                           # the next one, while you play

    def _on_move(self, move: chess.Move) -> None:
        if self.state == "analysis":
            self.analysis.play(move)
            return
        if self.state == "sac":
            sac_move = chess.Move.from_uci(self.cand.sac_uci)
            if move != sac_move:
                # not it: a miss (once), a hint — which piece — then Try again or Show the sacrifice
                shown = self.board.copy()
                shown.push(move)
                self.board_widget.show_board(shown)
                if not self._missed_here:
                    self.misses += 1
                    self._missed_here = True
                piece = chess.piece_name(self.board.piece_at(sac_move.from_square).piece_type)
                self.state = "sac_missed"
                self.status_label.configure(text=f"✗ {self.board.san(move)} isn't the sacrifice.", text_color=theme.ERROR)
                self.detail_label.configure(text=f"Hint: the sacrifice is a {piece} move.")
                self.show_button.configure(text="Show the sacrifice")
                self._show_buttons(self.retry_button, self.show_button)
                return
            found = f"✓ {self.board.san(move)}!! — "
            self._missed_here = False
            self._push(move)
            self._ask_reply(found)
        elif self.state == "move":
            shown = self.board.copy()
            shown.push(move)
            self.board_widget.show_board(shown)
            self.state = "judging"
            self.detail_label.configure(text="Checking…")
            self._jobs.put(("judge", self._token, self.board.fen(), move, self.step))

    def _push(self, move: chess.Move) -> None:
        self.board.push(move)
        self.line.append(move)
        self.board_widget.show_board(self.board.copy())

    def _ask_reply(self, prefix: str = "") -> None:
        self.state = "thinking"
        self.status_label.configure(text=f"{prefix}their best defence…" if prefix else "Their best defence…",
                                    text_color=SAC_COLOR if prefix else theme.TEXT_MUTED)
        self.detail_label.configure(text="")
        self._start_engine()
        self._jobs.put(("reply", self._token, self.board.fen(), self.me, self.moves_done))

    def _on_replied(self, reply: Optional[chess.Move], step: Optional[sac.Step]) -> None:
        if reply is None:                          # your move ended the game
            self._finish("mate" if self.board.is_checkmate() else "over", None)
            return
        token = self._token

        def show():
            if token is not self._token:
                return
            san = self.board.san(reply)
            self._push(reply)
            self.step = step
            self._missed_here = False
            if step.over:
                self._finish(step.reason, step.best_cp)
                return
            self.eval_bar.set(step.best_cp if self.me == chess.WHITE else -step.best_cp,
                              flipped=self.me == chess.BLACK)
            self.state = "move"
            self.status_label.configure(text=f"They played {san}. Your move — keep it going.", text_color=theme.TEXT)
            self.detail_label.configure(text=f"Follow-up {self.moves_done + 1}. Line so far: "
                                             f"{_numbered(self.start, self.line)}")

        self.after(REPLY_DELAY_MS, show)

    def _on_judged(self, move: chess.Move, right: bool, cp: int) -> None:
        if right:
            san = self.board.san(move)
            self._push(move)
            self.moves_done += 1
            self.status_label.configure(text=f"✓ {san}", text_color=theme.SUCCESS)
            if self.board.is_checkmate():
                self._finish("mate", cp)
                return
            self._ask_reply()
            return
        self.state = "missed"
        if not self._missed_here:
            self.misses += 1
            self._missed_here = True
        self.show_button.configure(text="Show the move")
        self.status_label.configure(text=f"✗ {self.board.san(move)} lets them off the hook.", text_color=theme.ERROR)
        self.detail_label.configure(text="Try again (a clean line scores more), or Show the move.")
        self._show_buttons(self.retry_button, self.show_button)

    def _try_again(self) -> None:
        if self.state == "sac_missed":                    # the sacrifice itself
            self.state = "sac"
            self.board_widget.show_board(self.board.copy())
            self.status_label.configure(text="Try again — find the sacrifice!!", text_color=SAC_COLOR)
            self._show_buttons()
            return
        if self.state != "missed":
            return
        self.state = "move"
        self.board_widget.show_board(self.board.copy())
        self.status_label.configure(text="Try again — your move.", text_color=theme.TEXT)
        self.detail_label.configure(text=f"Line so far: {_numbered(self.start, self.line)}")
        self._show_buttons()

    def _show_move(self) -> None:
        if self.state == "sac_missed":                    # reveal the sacrifice and play it
            sac_move = chess.Move.from_uci(self.cand.sac_uci)
            self.board_widget.show_board(self.board.copy())
            self.board_widget.set_marked_arrows([(sac_move.from_square, sac_move.to_square, SAC_COLOR)])
            self._show_buttons()
            self.state = "sac"
            self.after(800, lambda: self._on_move(sac_move) if self.state == "sac" else None)
            return
        if self.state != "missed" or self.step is None:
            return
        best = self.step.best
        self.board_widget.show_board(self.board.copy())
        self.board_widget.set_marked_arrows([(best.from_square, best.to_square, REPLY_COLOR)])
        self._show_buttons()
        self.state = "judging"
        self.after(700, lambda: self._on_judged(best, True, self.step.best_cp))

    def _finish(self, reason: str, cp: Optional[int]) -> None:
        self.state = "result"
        sac.record(self.conn, self.cand, self.moves_done, self.misses)
        progression.notify()
        clean = self.misses == 0
        s = self.session
        s["rounds"] += 1
        s["clean"] += clean
        s["streak"] = s["streak"] + 1 if clean else 0
        s["best"] = max(s["best"], s["streak"])
        evaluation = f" (Stockfish {cp / 100:+.1f})".replace("-", "−") if cp is not None and abs(cp) < 5000 else ""
        why = {"mate": "Checkmate!",
               "winning": f"You're clearly winning now{evaluation}.",
               "calm": f"The position has calmed down — the sacrifice paid off{evaluation}.",
               "max": f"A long line — and it's still working{evaluation}.",
               "over": "The game is over."}.get(reason, "")
        self.status_label.configure(
            text=("✓ You made it work!" if clean else f"Done — with {self.misses} hint{'s' if self.misses != 1 else ''}")
            + f"  {why}", text_color=theme.SUCCESS if clean else theme.WARNING)
        self._game_line, game_text = self._game_comparison()
        self.detail_label.configure(text=f"The line: {_numbered(self.start, self.line)}"
                                         + (f"\n{game_text}" if game_text else ""))
        if cp is not None and abs(cp) < 5000:
            self.eval_bar.set(cp if self.me == chess.WHITE else -cp, flipped=self.me == chess.BLACK)
        buttons = ([self.next_button, self.analyze_button]
                   + ([self.review_button] if self.cand.game_id and self.on_review_game else []))
        self._show_buttons(*buttons)
        self._refresh_scores()

    # ---------- what you played in the game ----------

    def _game_comparison(self):
        """(your game's moves from the sacrifice on, a summary): did you find the
        follow-ups in the real game? Judged like the round, from the game's saved
        analysis — and, like the round, only while the position was still sharp
        and not yet clearly won (after that the sacrifice had paid off)."""
        c = self.cand
        if c.game_id is None or c.ply is None:
            return [], ""
        row = self.conn.execute("SELECT pgn, result FROM games WHERE id = ?", (c.game_id,)).fetchone()
        game = chess.pgn.read_game(io.StringIO(row["pgn"])) if row else None
        if game is None:
            return [], ""
        moves = list(game.mainline_moves())[c.ply:c.ply + len(self.line) + 2]
        if c.source == "missed":
            return moves, self._missed_text(c, moves, row["result"])
        evals = {r["ply"]: r for r in self.conn.execute(
            "SELECT ply, move_uci, best_move_uci, cp_before, cp_after, cp_second, fen_before FROM move_evaluations "
            "WHERE game_id = ? AND ply BETWEEN ? AND ?", (c.game_id, c.ply, c.ply + len(moves)))}
        board, found, tried, slip, stopped, played = self.start.copy(), 0, 0, "", "", []
        for i, move in enumerate(moves):
            if move not in board.legal_moves:
                break
            san = board.san(move)
            mark = ""
            if i > 0 and i % 2 == 0 and not stopped:           # one of your follow-ups, while it still mattered
                ev = evals.get(c.ply + i)
                if ev is not None and ev["cp_after"] is not None:
                    if ev["cp_before"] >= sac.CLEAR_WIN_CP:
                        stopped = f" From move {board.fullmove_number} you were clearly winning ({ev['cp_before'] / 100:+.1f})."
                    elif ev["cp_second"] is not None and \
                            win_chances(ev["cp_before"]) - win_chances(ev["cp_second"]) < sac.CALM_GAP:
                        stopped = f" From move {board.fullmove_number} the position had calmed down."
                    else:
                        tried += 1
                        ok = (ev["move_uci"] == ev["best_move_uci"]
                              or win_chances(ev["cp_before"]) - win_chances(ev["cp_after"]) <= sac.OK_WC)
                        found += ok
                        mark = "✓" if ok else "?"
                        if not ok and not slip:
                            best = chess.Board(ev["fen_before"]).san(chess.Move.from_uci(ev["best_move_uci"]))
                            slip = (f" {self._number(board)}{san} let it slip "
                                    f"({ev['cp_before'] / 100:+.1f} → {ev['cp_after'] / 100:+.1f}; better was {best}).")
            elif i == 1 and len(self.line) > 1 and move != self.line[1]:
                mark = f" (Stockfish defends {board.san(self.line[1])})"
            played.append((board.copy(stack=False), san, mark))
            board.push(move)
        if not played:
            return [], ""
        parts = []
        for j, (b, san, mark) in enumerate(played):          # 10.Nxb5 d5 11.Nd6 — numbers on White's moves
            number = f"{b.fullmove_number}." if b.turn == chess.WHITE else (f"{b.fullmove_number}..." if j == 0 else "")
            parts.append(f"{number}{san}{mark}")
        result = {"win": "you won", "loss": "you lost", "draw": "it was drawn"}.get(row["result"], "")
        if tried:
            verdict = (f"You found {found} of {tried} follow-up{'s' if tried != 1 else ''}"
                       + (" — the right line." if found == tried else "."))
        elif stopped:
            verdict = "The sacrifice paid off straight away."
        else:
            verdict = "No saved analysis of these moves."
        return moves, (f"In your game: {' '.join(parts)}\n{verdict}{slip}{stopped}"
                       + (f" In the end {result}." if result else ""))

    def _missed_text(self, c: sac.Candidate, moves: List[chess.Move], result: str) -> str:
        """A sacrifice you missed: what you played instead, what it cost, how the game went on."""
        ev = self.conn.execute("SELECT move_uci, cp_before, cp_after FROM move_evaluations WHERE game_id = ? AND ply = ?",
                               (c.game_id, c.ply)).fetchone()
        if ev is None or not moves:
            return ""
        board = self.start.copy()
        played = board.san(chess.Move.from_uci(ev["move_uci"]))
        parts = []
        for i, move in enumerate(moves[:4]):
            if move not in board.legal_moves:
                break
            number = f"{board.fullmove_number}." if board.turn == chess.WHITE else (
                f"{board.fullmove_number}..." if i == 0 else "")
            parts.append(f"{number}{board.san(move)}")
            board.push(move)
        outcome = {"win": "you won", "loss": "you lost", "draw": "it was drawn"}.get(result, "")
        return (f"You missed it in your game: {self._number(self.start)}{played} instead "
                f"({ev['cp_before'] / 100:+.1f} → {ev['cp_after'] / 100:+.1f}); then {' '.join(parts[1:])}"
                + (f" … {outcome}." if outcome else " …"))

    @staticmethod
    def _number(board: chess.Board) -> str:
        return f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}..."

    def _analyse_game_line(self) -> None:
        """The analysis board on your game's line: from before the sacrifice, ▶ walks what you played."""
        if self.state != "analysis" or not self._game_line:
            return
        self.analysis.start(self.start, ahead=self._game_line)

    # ---------- analysis after the round ----------

    def _start_analysis(self) -> None:
        if self.state != "result":
            return
        self.state = "analysis"
        self._result_texts = (self.status_label.cget("text"), self.detail_label.cget("text"))
        self.status_label.pack_forget()
        self.detail_label.pack_forget()
        self.ideas_frame.pack(after=self._button_row, anchor="w", fill="x", pady=(10, 0))
        buttons = [self.back_step_button, self.fwd_step_button]
        if self._game_line:
            buttons.append(self.game_line_button)
        self._show_buttons(*buttons, self.done_button)
        self.analysis.start(self.board)                 # the whole line: ◀ goes back before the sacrifice

    def _stop_analysis(self) -> None:
        if self.state != "analysis":
            return
        self.state = "result"
        self.analysis.stop()
        self.ideas_frame.pack_forget()
        self.status_label.pack(after=self._button_row, anchor="w", pady=(10, 0))
        self.detail_label.pack(after=self.status_label, anchor="w", pady=(2, 0))
        self.status_label.configure(text=self._result_texts[0])
        self.detail_label.configure(text=self._result_texts[1])
        self.board_widget.show_board(self.board.copy())
        buttons = ([self.next_button, self.analyze_button]
                   + ([self.review_button] if self.cand.game_id and self.on_review_game else []))
        self._show_buttons(*buttons)

    def _analysis_title(self, board: chess.Board) -> str:
        side = "White" if board.turn == chess.WHITE else "Black"
        played = len(board.move_stack)
        where = ("before the sacrifice" if played == 0 else
                 "on the round's line" if board.move_stack == self.line[:played] else
                 "your game's line" if self._game_line and board.move_stack == self._game_line[:played] else
                 "your own line")
        return f"{side} to move · {where}"

    def _review_game(self) -> None:
        if self.cand is not None and self.cand.game_id and self.on_review_game:
            self.on_review_game(self.cand.game_id, self.cand.fen)

    # ---------- more Lichess sacrifices ----------

    def _add_lichess(self) -> None:
        low, high = lichess_puzzles.default_range(explorer.my_recent_rating(self.conn))
        low = int(db.get_setting(self.conn, "lichess_min_rating") or low)
        high = int(db.get_setting(self.conn, "lichess_max_rating") or high)
        skip = lichess_puzzles.ids(self.conn)
        stop = threading.Event()
        self.lichess_button.configure(state="disabled", text="Adding Lichess sacrifices…")

        def worker():
            batches, error = [], None
            try:
                lichess_puzzles.download(low, high, LICHESS_BATCH, skip, batches.append, stop, theme="sacrifice")
            except Exception as e:
                error = e
            self._results.put(("lichess", batches, error))

        threading.Thread(target=worker, daemon=True).start()

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
                    if job[0] == "prepare":
                        self._results.put(("prepared", job[1], sac.follow_ups(engine, job[1])))
                    elif job[0] == "reply":
                        _, token, fen, me, done = job
                        board = chess.Board(fen)
                        reply = sac.their_reply(engine, board)
                        step = None
                        if reply is not None:
                            board.push(reply)
                            step = sac.step(engine, board, me, done)
                        self._results.put(("replied", token, reply, step))
                    else:
                        _, token, fen, move, step = job
                        right, cp = sac.judge(engine, chess.Board(fen), move, step)
                        self._results.put(("judged", token, move, right, cp))
            except Exception as e:
                self._results.put(("error", str(e)))

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._results.get_nowait()
                kind = item[0]
                if kind == "pool":
                    self._set_pool(item[1])
                elif kind == "prepared":
                    self._on_prepared(item[1], item[2])
                elif kind == "replied":
                    _, token, reply, step = item
                    if token is self._token and self.state == "thinking":
                        self._on_replied(reply, step)
                elif kind == "judged":
                    _, token, move, right, cp = item
                    if token is self._token and self.state == "judging":
                        self._on_judged(move, right, cp)
                elif kind == "lichess":
                    _, batches, error = item
                    for b in batches:
                        lichess_puzzles.save(self.conn, b)
                    added = sum(len(b) for b in batches)
                    self.lichess_button.configure(state="normal", text=f"+ {LICHESS_BATCH} Lichess sacrifice puzzles")
                    if added and self._pool is not None:
                        fresh = sac.lichess_candidates(self.conn)
                        known = {c.key for c in self._pool}
                        self._pool = sac.pick(self._pool + [c for c in fresh if c.key not in known], sac.seen(self.conn))
                    self.pool_label.configure(text=f"Added {added} Lichess sacrifice puzzles." if not error else
                                              f"Couldn't reach Lichess ({type(error).__name__}).")
                    if self._waiting:
                        self._prep_tries = 0
                        self._prepare()
                else:
                    self.status_label.configure(text=f"Engine error: {item[1]}", text_color=theme.ERROR)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    # ---------- scores ----------

    def _refresh_scores(self) -> None:
        s = self.session
        self.session_label.configure(text=f"Streak {s['streak']}  ·  best {s['best']}" if s["rounds"] else "Streak 0")
        self.session_detail.configure(text=f"Clean lines {s['clean']}/{s['rounds']}" if s["rounds"] else
                                      "A round counts toward your streak when you find every follow-up yourself.")
        rounds, clean = sac.summary(self.conn)
        self.alltime_label.configure(text=f"Sacrifices made to work: {rounds} · clean {clean}" if rounds
                                     else "No rounds played yet.")
