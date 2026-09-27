"""Thin wrapper around the chess.com PubAPI for pulling your own games."""

from __future__ import annotations

import datetime as dt
from typing import Callable, List, Optional

import requests

from models import Game, GameResult, PlayerColor

BASE_URL = "https://api.chess.com/pub"
HEADERS = {"User-Agent": "chessprep-desktop/0.1 (personal use)"}


def get_archive_urls(username: str) -> List[str]:
    resp = requests.get(
        f"{BASE_URL}/player/{username}/games/archives", headers=HEADERS, timeout=15
    )
    resp.raise_for_status()
    return resp.json()["archives"]


def get_games_from_archive(archive_url: str, username: str) -> List[Game]:
    resp = requests.get(archive_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    raw_games = resp.json().get("games", [])

    games = []
    for g in raw_games:
        if "pgn" not in g:
            continue
        white = g["white"]["username"].lower()
        my_color = PlayerColor.WHITE if white == username.lower() else PlayerColor.BLACK
        opponent = g["black"]["username"] if my_color == PlayerColor.WHITE else g["white"]["username"]

        my_result_raw = g["white"]["result"] if my_color == PlayerColor.WHITE else g["black"]["result"]
        if my_result_raw == "win":
            result = GameResult.WIN
        elif my_result_raw in ("checkmated", "resigned", "timeout", "abandoned", "lose"):
            result = GameResult.LOSS
        else:
            result = GameResult.DRAW

        games.append(
            Game(
                chess_com_url=g["url"],
                pgn=g["pgn"],
                played_at=dt.datetime.fromtimestamp(g.get("end_time", 0)),
                time_control=g.get("time_control", "unknown"),
                my_color=my_color,
                opponent_username=opponent,
                result=result,
            )
        )
    return games


def fetch_all_games(
    username: str, months_back: int = 3,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[Game]:
    """Fetch games from the most recent `months_back` monthly archives
    (0 = your entire history). on_progress(done, total) is called after
    each month is downloaded."""
    archive_urls = get_archive_urls(username)
    if months_back > 0:
        archive_urls = archive_urls[-months_back:]
    all_games: List[Game] = []
    for i, url in enumerate(archive_urls, start=1):
        all_games.extend(get_games_from_archive(url, username))
        if on_progress:
            on_progress(i, len(archive_urls))
    return all_games
