"""Where files live, both when run from source (python gui.py) and when
packaged as a standalone .exe with PyInstaller.

- app_dir(): where YOUR data goes (chessprep.db, a stockfish/ folder you
  add yourself) — the project folder, or the folder holding the .exe.
- resource_dir(): read-only files bundled with the app (fonts, a bundled
  Stockfish) — the project folder, or PyInstaller's unpack directory.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
