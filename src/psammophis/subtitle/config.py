import os
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib.dotenv import load_dotenv_file, parse_dotenv

_PREFIX = "SUBTITLE_"

__all__ = ["Config", "ConfigError", "load_config", "parse_dotenv"]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    api_key: str | None
    username: str | None
    password: str | None
    languages: tuple[str, ...]
    user_agent: str
    media_server: str


def _get(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    full_key = _PREFIX + key
    return os.environ.get(full_key, env.get(full_key, default))


def load_config(env_path: str | Path = ".env") -> Config:
    env = load_dotenv_file(Path(env_path))
    languages_raw = _get(env, "LANGUAGES", "en") or "en"
    languages = tuple(item.strip() for item in languages_raw.split(",") if item.strip())
    if not languages:
        raise ConfigError(f"{_PREFIX}LANGUAGES must contain at least one language")
    media_server = (_get(env, "SERVER", "plex") or "plex").lower()
    if media_server not in ("plex", "jellyfin"):
        raise ConfigError(f"{_PREFIX}SERVER must be 'plex' or 'jellyfin'")
    return Config(
        _get(env, "OPENSUBTITLES_API_KEY"),
        _get(env, "OPENSUBTITLES_USERNAME"),
        _get(env, "OPENSUBTITLES_PASSWORD"),
        languages,
        _get(env, "USER_AGENT", "subtitle/0.1") or "subtitle/0.1",
        media_server,
    )
