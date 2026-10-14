"""UI glue for the Lichess Opening Explorer: the Connect dialog, and one
shared background downloader so the app never sends Lichess more than one
request at a time (and never touches Tk or SQLite off the main thread)."""

from __future__ import annotations

import queue
import webbrowser
from tkinter import messagebox
from typing import Callable, List, Optional

import customtkinter as ctk

import explorer
import theme

POLL_MS = 100


class _Downloader:
    """Jobs run one after another; each job's positions are fetched on a
    background thread, saved to the cache on the UI thread, then its
    on_done(fetched, error) runs on the UI thread too."""

    def __init__(self):
        self._jobs: List[tuple] = []
        self._running = False
        self._events: "queue.Queue" = queue.Queue()
        self._widget = None

    def start(self, widget, conn, fens: List[str], on_done: Callable[[int, Optional[str]], None],
              on_progress: Optional[Callable[[int, int], None]] = None) -> int:
        """Queues the positions not cached yet; returns how many that is.
        on_done runs right away (with 0) when there's nothing to fetch;
        on_progress(done, total) after each downloaded position."""
        token = explorer.get_token(conn)
        if not token:
            on_done(0, "Not connected to Lichess.")
            return 0
        bands = explorer.rating_bands(conn)
        todo = explorer.missing(conn, fens, bands)
        if not todo:
            on_done(0, None)
            return 0
        self._jobs.append((widget, conn, token, todo, bands, on_done, on_progress))
        if not self._running:
            self._next()
        return len(todo)

    def _next(self) -> None:
        if not self._jobs:
            self._running = False
            return
        self._running = True
        widget, conn, token, todo, bands, on_done, on_progress = self._jobs.pop(0)
        self._widget, done = widget, [0]

        def on_result(fen, data):
            self._events.put(("result", conn, fen, bands, data))

        def finished(fetched, error):
            self._events.put(("done", on_done, fetched, error))

        explorer.warm(token, todo, bands, on_result, finished)
        self._poll(len(todo), done, on_progress)

    def _poll(self, total: int, done: List[int], on_progress) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "result":
                    _, conn, fen, bands, data = event
                    try:
                        explorer.save(conn, fen, bands, data)
                    except Exception:
                        pass   # player switched (old connection closed) — just drop it
                    done[0] += 1
                    if on_progress:
                        try:
                            on_progress(done[0], total)
                        except Exception:
                            pass
                else:
                    _, on_done, fetched, error = event
                    try:
                        on_done(fetched, error)
                    except Exception:
                        pass   # its tab was destroyed (player switched)
                    self._next()
                    return
        except queue.Empty:
            pass
        try:
            self._widget.after(POLL_MS, lambda: self._poll(total, done, on_progress))
        except Exception:
            self._running = False   # widget gone; the thread finishes on its own


downloader = _Downloader()


def connect_dialog(parent, conn, on_change: Callable[[], None]) -> None:
    """Paste a Lichess personal access token, check it, and save it."""
    dialog = ctk.CTkToplevel(parent)
    dialog.title("Connect Lichess")
    dialog.configure(fg_color=theme.BG)
    dialog.transient(parent.winfo_toplevel())
    dialog.after(50, dialog.lift)

    body = ctk.CTkFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=20, pady=16)
    theme.label(body, "Connect Lichess", "heading").pack(anchor="w")
    theme.label(
        body,
        "The Lichess Opening Explorer needs a free personal access token:\n"
        "1. Sign in at lichess.org and open the token page (button below).\n"
        "2. Click New personal access token, name it King Coach, leave every\n"
        "    permission box unchecked, and create it.\n"
        "3. Copy the token and paste it here.\n\n"
        "It's saved only in this player's local database (never uploaded to GitHub).",
        "muted", wraplength=440,
    ).pack(anchor="w", pady=(4, 10))
    theme.button(body, "Open the Lichess token page", lambda: webbrowser.open(explorer.TOKEN_PAGE),
                 height=32).pack(anchor="w", pady=(0, 10))

    token_var = ctk.StringVar()
    entry = ctk.CTkEntry(body, textvariable=token_var, show="•", width=440, height=36,
                         fg_color=theme.INPUT_BG, border_color=theme.BORDER_STRONG,
                         text_color=theme.TEXT, font=theme.FONT_BASE)
    entry.pack(fill="x")
    entry.focus_set()
    status = theme.label(body, "", "muted", wraplength=440)
    status.pack(anchor="w", pady=(6, 0))

    def save(event=None):
        token = token_var.get().strip()
        if not token:
            status.configure(text="Paste the token first.", text_color=theme.ERROR)
            return
        status.configure(text="Checking with Lichess…", text_color=theme.TEXT_MUTED)
        dialog.update_idletasks()
        try:
            username = explorer.check_token(token)
        except explorer.ExplorerError as e:
            status.configure(text=str(e), text_color=theme.ERROR)
            return
        explorer.set_token(conn, token)
        dialog.destroy()
        messagebox.showinfo("Lichess connected", f"Connected as {username}.", parent=parent)
        on_change()

    buttons = ctk.CTkFrame(body, fg_color="transparent")
    buttons.pack(fill="x", pady=(12, 0))
    theme.button(buttons, "Cancel", dialog.destroy, "ghost", width=90).pack(side="right")
    theme.button(buttons, "Check & save", save, "primary", width=130).pack(side="right", padx=(0, 8))
    entry.bind("<Return>", save)


def disconnect(parent, conn, on_change: Callable[[], None]) -> None:
    if messagebox.askyesno("Disconnect Lichess",
                           "Remove the saved Lichess token? Downloaded opening data is kept.",
                           parent=parent):
        explorer.set_token(conn, None)
        on_change()
