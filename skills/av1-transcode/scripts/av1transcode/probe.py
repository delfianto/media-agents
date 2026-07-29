"""ffprobe wrapper, normalized into the shape presets.py/colorinfo.py/command.py
consume. Read-only: this module never touches the media file it inspects.

`-show_streams` alone (no extra flags) was confirmed against a real 4K remux
in this library to fully populate color_primaries/color_transfer/color_space
as plain strings *and* side_data_list with "Mastering display metadata",
"Content light level metadata", and "DOVI configuration record" entries
directly on the video stream -- no `-show_frames`/`-read_intervals` decode
needed to reach any of it.
"""

import json
import subprocess
from pathlib import Path

_MASTERING_DISPLAY = "Mastering display metadata"
_CONTENT_LIGHT = "Content light level metadata"
_DOVI_CONFIG = "DOVI configuration record"


def probe_file(path: str | Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)

    fmt = data.get("format", {}) or {}
    video = None
    audio = []
    subtitles = []
    for s in data.get("streams", []):
        codec_type = s.get("codec_type")
        disposition = s.get("disposition", {}) or {}
        if codec_type == "video" and video is None and not disposition.get("attached_pic"):
            video = _normalize_video(s)
        elif codec_type == "audio":
            audio.append(_normalize_audio(s))
        elif codec_type == "subtitle":
            subtitles.append(
                {
                    "index": s.get("index"),
                    "codec_name": s.get("codec_name"),
                    "language": (s.get("tags") or {}).get("language"),
                }
            )

    return {
        "path": str(path),
        "format": {
            "duration": _to_float(fmt.get("duration")),
            "size": _to_int(fmt.get("size")),
            "bit_rate": _to_int(fmt.get("bit_rate")),
        },
        "video": video,
        "audio": audio,
        "subtitles": subtitles,
    }


def _find_side_data(side_data_list: list[dict], side_data_type: str) -> dict | None:
    return next((sd for sd in side_data_list if sd.get("side_data_type") == side_data_type), None)


def _normalize_video(s: dict) -> dict:
    side_data_list = s.get("side_data_list", []) or []
    return {
        "index": s.get("index"),
        "codec_name": s.get("codec_name"),
        "profile": s.get("profile"),
        "width": s.get("width"),
        "height": s.get("height"),
        "pix_fmt": s.get("pix_fmt"),
        "bit_rate": _to_int(s.get("bit_rate")),
        "color_primaries": s.get("color_primaries"),
        "color_transfer": s.get("color_transfer"),
        "color_space": s.get("color_space"),
        "mastering_display": _find_side_data(side_data_list, _MASTERING_DISPLAY),
        "content_light": _find_side_data(side_data_list, _CONTENT_LIGHT),
        "dolby_vision": _find_side_data(side_data_list, _DOVI_CONFIG),
    }


def _normalize_audio(s: dict) -> dict:
    return {
        "index": s.get("index"),
        "codec_name": s.get("codec_name"),
        "channels": s.get("channels") or 2,
        "channel_layout": s.get("channel_layout"),
        "bit_rate": _to_int(s.get("bit_rate")),
        "language": (s.get("tags") or {}).get("language"),
    }


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except TypeError, ValueError:
        return None


def _to_int(v) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except TypeError, ValueError:
        return None
