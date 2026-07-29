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
    attachment_count = 0
    for s in data.get("streams", []):
        codec_type = s.get("codec_type")
        disposition = s.get("disposition", {}) or {}
        if codec_type == "video" and video is None and not disposition.get("attached_pic"):
            video = _normalize_video(s)
        elif codec_type == "audio":
            audio.append(_normalize_audio(s))
        elif codec_type == "subtitle":
            subtitles.append(_normalize_subtitle(s))
        elif codec_type == "attachment":
            attachment_count += 1

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
        "attachment_count": attachment_count,
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
    tags = s.get("tags", {}) or {}
    return {
        "index": s.get("index"),
        "codec_name": s.get("codec_name"),
        "profile": s.get("profile"),
        "channels": s.get("channels") or 2,
        "channel_layout": s.get("channel_layout"),
        # ffprobe's own bit_rate field is frequently absent for VBR lossless
        # codecs (TrueHD in particular) since there's no single header value
        # to report -- the container's own BPS tag (the same fallback
        # media-library's scan.py uses) fills that gap.
        "bit_rate": _to_int(s.get("bit_rate")) or _to_int(tags.get("BPS")),
        "language": tags.get("language"),
        "title": tags.get("title"),
    }


def _normalize_subtitle(s: dict) -> dict:
    tags = s.get("tags", {}) or {}
    disposition = s.get("disposition", {}) or {}
    return {
        "index": s.get("index"),
        "codec_name": s.get("codec_name"),
        "language": tags.get("language"),
        "title": tags.get("title"),
        "hearing_impaired": bool(disposition.get("hearing_impaired")),
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
