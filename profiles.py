"""Player profiles: each player gets their OWN database file, so two people
sharing the app never mix games, analysis, puzzles, repertoires, stats or
settings.

profiles.json (next to the databases) lists the profiles and which one was
used last. The original chessprep.db becomes the first profile, so nothing
existing is lost or moved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from paths import app_dir

REGISTRY = "profiles.json"
LEGACY_DB = "chessprep.db"


def _registry_path() -> Path:
    return app_dir() / REGISTRY


def _load() -> Dict:
    path = _registry_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("profiles"):
                return data
        except (ValueError, OSError):
            pass
    # first run: the existing database (if any) becomes the first profile
    name = _legacy_username() or "Player 1"
    data = {"current": name, "profiles": {name: LEGACY_DB}}
    _save(data)
    return data


def _legacy_username() -> str:
    """The chess.com username stored in the original database, if any."""
    db_file = app_dir() / LEGACY_DB
    if not db_file.exists():
        return ""
    import sqlite3
    try:
        conn = sqlite3.connect(db_file)
        row = conn.execute("SELECT value FROM settings WHERE key = 'chess_com_username'").fetchone()
        conn.close()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""


def _save(data: Dict) -> None:
    _registry_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_profiles() -> List[str]:
    return list(_load()["profiles"])


def current() -> str:
    data = _load()
    return data["current"] if data["current"] in data["profiles"] else next(iter(data["profiles"]))


def db_path(name: str) -> Path:
    return app_dir() / _load()["profiles"][name]


def set_current(name: str) -> None:
    data = _load()
    if name in data["profiles"]:
        data["current"] = name
        _save(data)


def add(name: str) -> str:
    """Creates a profile (with its own new database file) and returns its
    name. Raises ValueError for an empty or duplicate name."""
    name = name.strip()
    data = _load()
    if not name:
        raise ValueError("Enter a name.")
    if name.lower() in (p.lower() for p in data["profiles"]):
        raise ValueError(f"A player called '{name}' already exists.")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "player"
    filename, n = f"chessprep_{slug}.db", 2
    used = set(data["profiles"].values())
    while filename in used or (app_dir() / filename).exists():
        filename, n = f"chessprep_{slug}_{n}.db", n + 1
    data["profiles"][name] = filename
    _save(data)
    return name
