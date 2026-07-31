import json
import math
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class VideoInfo:
    path: str
    duration: float
    size: int
    bit_rate: int | None
    width: int
    height: int
    frame_rate: float
    frame_count: int | None
    pix_fmt: str | None
    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    color_range: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in {"smpte2084", "arib-std-b67"}


def _to_float(value: object) -> float | None:
    if value in {None, "", "N/A", "0/0"}:
        return None
    try:
        parsed = float(str(value))
    except TypeError, ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _to_int(value: object) -> int | None:
    parsed = _to_float(value)
    return int(parsed) if parsed is not None else None


def _frame_rate(value: object) -> float:
    try:
        rate = float(Fraction(str(value)))
    except ZeroDivisionError, ValueError:
        rate = 0.0
    if rate <= 0:
        raise ValueError(f"invalid video frame rate: {value!r}")
    return rate


def probe_video(path: Path) -> VideoInfo:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "format=duration,size,bit_rate:"
                "stream=width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_frames,"
                "color_primaries,color_transfer,color_space,color_range:"
                "stream_tags=NUMBER_OF_FRAMES"
            ),
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"no primary video stream found in {path}")
    stream = streams[0]
    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    size = _to_int(fmt.get("size"))
    if duration is None or duration <= 0 or size is None:
        raise ValueError(f"missing duration or size for {path}")
    tags = stream.get("tags") or {}
    frame_count = _to_int(stream.get("nb_frames")) or _to_int(tags.get("NUMBER_OF_FRAMES"))
    return VideoInfo(
        path=str(path),
        duration=float(duration),
        size=int(size),
        bit_rate=_to_int(fmt.get("bit_rate")),
        width=int(stream["width"]),
        height=int(stream["height"]),
        frame_rate=_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        frame_count=int(frame_count) if frame_count is not None else None,
        pix_fmt=stream.get("pix_fmt"),
        color_primaries=stream.get("color_primaries"),
        color_transfer=stream.get("color_transfer"),
        color_space=stream.get("color_space"),
        color_range=stream.get("color_range"),
    )


def validate_alignment(reference: VideoInfo, distorted: VideoInfo) -> list[str]:
    errors: list[str] = []
    if (reference.width, reference.height) != (distorted.width, distorted.height):
        errors.append(
            f"resolution differs: reference {reference.width}x{reference.height}, "
            f"distorted {distorted.width}x{distorted.height}"
        )
    rate_delta = abs(reference.frame_rate - distorted.frame_rate)
    if rate_delta > max(0.001, reference.frame_rate * 0.0001):
        errors.append(
            f"frame rate differs: reference {reference.frame_rate:.6f}, "
            f"distorted {distorted.frame_rate:.6f}"
        )
    duration_delta = abs(reference.duration - distorted.duration)
    if duration_delta > max(1.0, reference.duration * 0.002):
        errors.append(
            f"duration differs by {duration_delta:.3f}s; align timelines before comparison"
        )
    if (
        reference.frame_count is not None
        and distorted.frame_count is not None
        and reference.frame_count != distorted.frame_count
    ):
        errors.append(
            f"frame count differs: reference {reference.frame_count}, "
            f"distorted {distorted.frame_count}"
        )
    if reference.color_transfer != distorted.color_transfer:
        errors.append(
            f"transfer differs: reference {reference.color_transfer}, "
            f"distorted {distorted.color_transfer}"
        )
    return errors
