"""Shared `.env`-style KEY=VALUE parsing. Stdlib only -- this format is
small enough that pulling in python-dotenv as a dependency isn't worth it.
Each skill's own config.py still owns its `AV1TRANSCODE_`/`MEDIAORGANIZER_`
prefix, required-key validation, and typed Config dataclass; this module
only owns the shared "read KEY=VALUE lines from a file" mechanics.
"""

from pathlib import Path


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


def load_dotenv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))
