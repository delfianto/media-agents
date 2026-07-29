"""Optional `.env`-file configuration -- mirrors media-organizer's
config.py pattern (KEY=VALUE lines, real process environment always
overrides the file) but everything here is optional: unlike
media-organizer, nothing this skill does needs an external credential, so
there's no equivalent of a required TMDB_API_KEY. `.env` just lets a
preferred output directory / default languages / bitrate-cap fraction
persist across invocations instead of being retyped as CLI flags every
time -- any flag explicitly passed on the command line still wins over
both the real environment and the file (see cli.py's `_resolve` helper).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .presets import MAX_BITRATE_FRACTION_OF_SOURCE

_PREFIX = "AV1TRANSCODE_"


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
    env = _load_dotenv_file(Path(env_path))

    output_dir_raw = _get(env, "OUTPUT_DIR")
    audio_lang = _get(env, "AUDIO_LANG", "eng") or "eng"
    subtitle_lang = _get(env, "SUBTITLE_LANG", "eng") or "eng"

    max_bitrate_fraction_raw = _get(env, "MAX_BITRATE_FRACTION")
    max_bitrate_fraction = (
        float(max_bitrate_fraction_raw)
        if max_bitrate_fraction_raw is not None
        else MAX_BITRATE_FRACTION_OF_SOURCE
    )

    return Config(
        output_dir=Path(output_dir_raw) if output_dir_raw else None,
        audio_lang=audio_lang,
        subtitle_lang=subtitle_lang,
        max_bitrate_fraction=max_bitrate_fraction,
    )
