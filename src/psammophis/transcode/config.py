"""Optional `.env`-file configuration -- mirrors organize's
config.py pattern (KEY=VALUE lines, real process environment always
overrides the file) but everything here is optional: unlike
organize, nothing this skill does needs an external credential, so
there's no equivalent of a required TMDB_API_KEY. `.env` just lets a
preferred output directory / default languages / bitrate-cap fraction
persist across invocations instead of being retyped as CLI flags every
time -- any flag explicitly passed on the command line still wins over
both the real environment and the file (see cli.py's `_resolve` helper).

The actual KEY=VALUE parsing lives in medialib.dotenv (shared with
organize's identical config.py pattern); `parse_dotenv` is re-exported
here so it stays part of this module's public API for existing callers/tests.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib.av1_presets import MAX_BITRATE_FRACTION_OF_SOURCE
from psammophis.medialib.dotenv import load_dotenv_file, parse_dotenv

_PREFIX = "TRANSCODE_"

__all__ = ["Config", "ConfigError", "load_config", "parse_dotenv"]


class ConfigError(ValueError):
    pass


def _get(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    full_key = _PREFIX + key
    return os.environ.get(full_key, env.get(full_key, default))


@dataclass(frozen=True)
class Config:
    output_dir: Path | None
    audio_lang: str
    subtitle_lang: str
    max_bitrate_fraction: float


def load_config(env_path: str | Path = ".env") -> Config:
    env = load_dotenv_file(Path(env_path))

    output_dir_raw = _get(env, "OUTPUT_DIR")
    audio_lang = _get(env, "AUDIO_LANG", "eng") or "eng"
    subtitle_lang = _get(env, "SUBTITLE_LANG", "eng") or "eng"

    max_bitrate_fraction_raw = _get(env, "MAX_BITRATE_FRACTION")
    try:
        max_bitrate_fraction = (
            float(max_bitrate_fraction_raw)
            if max_bitrate_fraction_raw is not None
            else MAX_BITRATE_FRACTION_OF_SOURCE
        )
    except ValueError as exc:
        raise ConfigError("TRANSCODE_MAX_BITRATE_FRACTION must be a number") from exc
    if not 0 < max_bitrate_fraction <= 1:
        raise ConfigError("TRANSCODE_MAX_BITRATE_FRACTION must be greater than 0 and at most 1")

    return Config(
        output_dir=Path(output_dir_raw) if output_dir_raw else None,
        audio_lang=audio_lang,
        subtitle_lang=subtitle_lang,
        max_bitrate_fraction=max_bitrate_fraction,
    )
