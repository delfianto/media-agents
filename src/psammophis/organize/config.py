import os
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib.dotenv import load_dotenv_file, parse_dotenv

from .matching import MIN_AUTO_CONFIDENCE

_PREFIX = "ORGANIZE_"

__all__ = ["Config", "ConfigError", "load_config", "parse_dotenv"]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    tmdb_api_key: str
    media_server: str
    movies_dir: Path
    tv_shows_dir: Path
    inbox_dir: Path
    min_confidence: float
    user_agent: str


def _get(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    full_key = _PREFIX + key
    return os.environ.get(full_key, env.get(full_key, default))


def _shared_tmdb_key(env: dict[str, str]) -> str | None:
    return _get(env, "TMDB_API_KEY") or os.environ.get("TMDB_API_KEY", env.get("TMDB_API_KEY"))


def load_config(env_path: str | Path = ".env") -> Config:
    env = load_dotenv_file(Path(env_path))
    tmdb_api_key = _shared_tmdb_key(env)
    if not tmdb_api_key:
        raise ConfigError(
            f"{_PREFIX}TMDB_API_KEY or TMDB_API_KEY is required "
            f"(set it in the environment or in {env_path})"
        )

    media_server = (_get(env, "SERVER", "plex") or "plex").lower()
    if media_server not in ("plex", "jellyfin"):
        raise ConfigError(f"{_PREFIX}SERVER must be 'plex' or 'jellyfin', got {media_server!r}")

    movies_dir = _get(env, "MOVIES_DIR")
    tv_shows_dir = _get(env, "TV_SHOWS_DIR")
    inbox_dir = _get(env, "INBOX_DIR")
    if not movies_dir or not tv_shows_dir or not inbox_dir:
        raise ConfigError(
            f"{_PREFIX}MOVIES_DIR, {_PREFIX}TV_SHOWS_DIR, and {_PREFIX}INBOX_DIR are all required"
        )

    min_confidence_raw = _get(env, "MIN_CONFIDENCE")
    try:
        min_confidence = float(min_confidence_raw) if min_confidence_raw else MIN_AUTO_CONFIDENCE
    except ValueError as exc:
        raise ConfigError(f"{_PREFIX}MIN_CONFIDENCE must be a number") from exc
    if not 0.0 <= min_confidence <= 1.0:
        raise ConfigError(f"{_PREFIX}MIN_CONFIDENCE must be between 0 and 1")

    return Config(
        tmdb_api_key=tmdb_api_key,
        media_server=media_server,
        movies_dir=Path(movies_dir),
        tv_shows_dir=Path(tv_shows_dir),
        inbox_dir=Path(inbox_dir),
        min_confidence=min_confidence,
        user_agent=_get(env, "USER_AGENT", "organize/0.1") or "organize/0.1",
    )
