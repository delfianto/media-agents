import os
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib.dotenv import load_dotenv_file, parse_dotenv

_PREFIX = "ARTWORK_"

__all__ = ["Config", "ConfigError", "load_config", "parse_dotenv"]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    tmdb_api_key: str
    media_server: str
    user_agent: str


def _get(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    full_key = _PREFIX + key
    return os.environ.get(full_key, env.get(full_key, default))


def load_config(env_path: str | Path = ".env") -> Config:
    env = load_dotenv_file(Path(env_path))
    tmdb_api_key = _get(env, "TMDB_API_KEY") or os.environ.get(
        "TMDB_API_KEY", env.get("TMDB_API_KEY")
    )
    if not tmdb_api_key:
        raise ConfigError(f"{_PREFIX}TMDB_API_KEY or TMDB_API_KEY is required")
    media_server = (_get(env, "SERVER", "plex") or "plex").lower()
    if media_server not in ("plex", "jellyfin"):
        raise ConfigError(f"{_PREFIX}SERVER must be 'plex' or 'jellyfin'")
    return Config(
        tmdb_api_key,
        media_server,
        _get(env, "USER_AGENT", "artwork/0.1") or "artwork/0.1",
    )
