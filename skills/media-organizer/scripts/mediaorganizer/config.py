"""Runtime configuration from a `.env` file plus the real process
environment (which always wins over `.env`, so an automated harness can
override a value without editing the file on disk). Stdlib only -- parsing
`KEY=VALUE` lines doesn't need python-dotenv as a dependency.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .matching import MIN_AUTO_CONFIDENCE

_PREFIX = "MEDIAORGANIZER_"


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _load_dotenv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    tmdb_api_key: str
    media_server: str  # "plex" | "jellyfin"
    movies_dir: Path
    tv_shows_dir: Path
    inbox_dir: Path
    opensubtitles_api_key: str | None
    opensubtitles_username: str | None
    opensubtitles_password: str | None
    subtitle_languages: tuple[str, ...]
    min_confidence: float
    user_agent: str


def _get(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    full_key = _PREFIX + key
    return os.environ.get(full_key, env.get(full_key, default))


def load_config(env_path: str | Path = ".env") -> Config:
    env = _load_dotenv_file(Path(env_path))

    tmdb_api_key = _get(env, "TMDB_API_KEY")
    if not tmdb_api_key:
        raise ConfigError(
            f"{_PREFIX}TMDB_API_KEY is required (set it in the environment or in {env_path}) -- "
            "get a free key at https://www.themoviedb.org/settings/api"
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

    languages_raw = _get(env, "SUBTITLE_LANGUAGES", "en") or "en"
    subtitle_languages = tuple(lang.strip() for lang in languages_raw.split(",") if lang.strip())

    min_confidence_raw = _get(env, "MIN_CONFIDENCE")
    min_confidence = float(min_confidence_raw) if min_confidence_raw else MIN_AUTO_CONFIDENCE

    return Config(
        tmdb_api_key=tmdb_api_key,
        media_server=media_server,
        movies_dir=Path(movies_dir),
        tv_shows_dir=Path(tv_shows_dir),
        inbox_dir=Path(inbox_dir),
        opensubtitles_api_key=_get(env, "OPENSUBTITLES_API_KEY"),
        opensubtitles_username=_get(env, "OPENSUBTITLES_USERNAME"),
        opensubtitles_password=_get(env, "OPENSUBTITLES_PASSWORD"),
        subtitle_languages=subtitle_languages,
        min_confidence=min_confidence,
        user_agent=_get(env, "USER_AGENT", "media-organizer/0.1") or "media-organizer/0.1",
    )
