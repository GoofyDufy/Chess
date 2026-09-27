"""
Finds a Stockfish binary without requiring the user to set an environment
variable every session. Checks, in order:

  1. STOCKFISH_PATH environment variable, if set
  2. A 'stockfish' folder inside this project (e.g. chessprep/stockfish/...)
  3. A few common install locations (Homebrew on Mac, PATH lookup)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from paths import app_dir, resource_dir


def find_stockfish() -> Optional[str]:
    # 1. Explicit override always wins
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # 2. Look inside a 'stockfish' subfolder of the project for any
    #    executable whose name starts with 'stockfish' (covers Windows
    #    .exe builds and Mac/Linux binaries with version-specific names)
    #    (next to the project / the .exe first, then one bundled inside the .exe)
    for stockfish_dir in (app_dir() / "stockfish", resource_dir() / "stockfish"):
        if stockfish_dir.is_dir():
            for path in stockfish_dir.rglob("stockfish*"):
                if path.is_file() and (os.access(path, os.X_OK) or path.suffix.lower() == ".exe"):
                    return str(path)

    # 3. Common install locations
    common_paths = [
        "/usr/local/bin/stockfish",
        "/opt/homebrew/bin/stockfish",
        "/usr/bin/stockfish",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p

    # 4. Anything on PATH
    found = shutil.which("stockfish")
    if found:
        return found

    return None
