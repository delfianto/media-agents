"""Wraps guessit's filename parsing into the small, stable shape the rest of
this skill needs. guessit is this skill's one third-party dependency
(pure-Python, no heavy deps) -- parsing scene-release filenames correctly is
exactly the multi-year accumulated-edge-case problem FileBot's matching
engine also has to solve, and a from-scratch regex parser would only ever
cover a fraction of what guessit already handles (multi-episode files,
anime release-group bracket conventions, unusual tag ordering, ...). This
module is the only place that imports it, so nothing else needs to know
guessit's own output shape.
"""

from dataclasses import dataclass
from pathlib import Path

from guessit import guessit


@dataclass(frozen=True)
class ParsedName:
    kind: str  # "movie" | "episode"
    title: str
    year: int | None
    season: int | None
    episode: int | None  # first episode number, for multi-episode files
    episode_title: str | None
    tmdb_id: int | None  # already embedded in the filename, e.g. "{tmdb-12345}"


def _first(value):
    """guessit returns a list instead of a scalar for some fields on
    multi-episode files (e.g. episode=[5, 6] for "S02E05-E06") -- take the
    first value consistently rather than letting int() crash on a list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse(path: str | Path) -> ParsedName | None:
    """None if guessit can't tell movie from episode, or found no title at
    all -- callers should treat that as "needs manual handling", not guess."""
    name = Path(path).name
    guess = guessit(name)

    kind = guess.get("type")
    if kind not in ("movie", "episode"):
        return None

    title = guess.get("title")
    if not title:
        return None

    season = _first(guess.get("season"))
    episode = _first(guess.get("episode"))
    year = guess.get("year")
    tmdb_id = guess.get("tmdb_id")

    return ParsedName(
        kind=kind,
        title=str(title),
        year=int(year) if year else None,
        season=int(season) if season is not None else None,
        episode=int(episode) if episode is not None else None,
        episode_title=str(guess["episode_title"]) if guess.get("episode_title") else None,
        tmdb_id=int(tmdb_id) if tmdb_id else None,
    )
