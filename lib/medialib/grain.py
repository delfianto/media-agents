"""Sampled grain/noise estimation via a denoise-diff SSIM probe.

Real per-pixel noise -- film grain, or the synthetic dither some anime BD
encodes add on purpose to hide gradient banding -- costs bits under plain
CRF/CQ and is exactly what SVT-AV1's `--film-grain` synthesis (denoise before
encoding, resynthesize a statistically matched grain pattern at decode) exists
to handle efficiently; `av1_nvenc` has no equivalent. Measuring how much a
denoise filter actually removes from a real sample of the file -- rather than
guessing from resolution/HDR/source age -- is what lets `av1_backend.
choose_backend` route heavily grainy/noisy sources to cpu and clean ones to
nvenc under `--backend auto`.

ffmpeg's own `bitplanenoise` filter (a purpose-built noise estimator, cheaper
than this module's approach) was tried first and rejected: measured directly
against three real files in this library --  A.I. Artificial Intelligence
(2001, 35mm scan), Mission Impossible - The Final Reckoning (2025, digital
4K), and a SPY x FAMILY episode (flat digital anime) -- it saturated near its
ceiling for almost every compressed source regardless of actual grain (LSB
noise alone put all three above 0.7 on a 0-1 scale, with overlapping ranges),
and at higher bit planes it tracked edge sharpness/fine detail rather than
grain -- the sharp-lined anime episode scored *noisier* than the actual
grainy 35mm scan. Denoise-diff doesn't have that problem: hqdn3d is tuned to
suppress high-frequency low-amplitude noise while preserving edges, so how
much it changes a sample (measured via ssim) reflects noise specifically, and
separated the same three files consistently across multiple sample points
each (1 - Y-SSIM, lower = cleaner): anime ~0.007-0.012, the 35mm scan
~0.012-0.015, the digital 4K remux ~0.015-0.017.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# luma_spatial:chroma_spatial:luma_tmp:chroma_tmp -- moderate strength,
# strong enough to remove grain-scale noise without eating real edges.
# Temporal denoising needs more than one frame of context, which every
# sample window (SAMPLE_DURATION_SECONDS at real framerates) provides.
_HQDN3D_PARAMS = "6:4:6:4"

SAMPLE_DURATION_SECONDS = 2.0
# Fractions of total duration to sample at -- avoids opening titles/credits
# at either end, and spreads samples so one unusually dark/bright/busy scene
# doesn't decide the whole file's grain score.
SAMPLE_FRACTIONS: tuple[float, ...] = (0.2, 0.5, 0.8)

# Provisional: the midpoint of the three-title measurement described above
# (anime ~0.009 avg, the 35mm scan ~0.014 avg, the digital 4K remux ~0.016
# avg), picked so both non-anime titles route to cpu and the anime title
# routes to nvenc. Only three titles' worth of real data went into this --
# recalibrate (`--grain-threshold` in av1-transcode/analyze) once it's been
# checked against more of this library's actual files, the same way
# track_policy's SDH/anime thresholds in track-strip were tuned after
# whole-library auditing rather than trusted on first measurement.
GRAIN_CPU_THRESHOLD = 0.012

_SSIM_ALL_RE = re.compile(r"SSIM.*\bAll:([\d.]+)")


@dataclass(frozen=True)
class GrainMeasurement:
    score: float  # 0..1, higher = grainier/noisier (1 - average sample SSIM)
    samples: tuple[float, ...]  # per-sample scores, same scale as score


def _sample_offsets(
    duration: float, fractions: tuple[float, ...], sample_duration: float
) -> list[float]:
    offsets = []
    for fraction in fractions:
        offset = duration * fraction
        offset = min(offset, max(0.0, duration - sample_duration))
        offsets.append(max(0.0, offset))
    return offsets


def _measure_sample(path: Path, offset: float, duration: float) -> float | None:
    cmd = [
        "ffmpeg",
        "-v",
        "info",
        "-nostdin",
        "-ss",
        str(offset),
        "-t",
        str(duration),
        "-i",
        str(path),
        "-filter_complex",
        f"[0:v]format=yuv420p,split=2[a][b];[b]hqdn3d={_HQDN3D_PARAMS}[den];[a][den]ssim",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except OSError, subprocess.TimeoutExpired:
        return None
    match = _SSIM_ALL_RE.search(proc.stderr)
    if not match:
        return None
    return max(0.0, 1.0 - float(match.group(1)))


def measure_grain(
    path: str | Path,
    duration: float | None,
    fractions: tuple[float, ...] = SAMPLE_FRACTIONS,
    sample_duration: float = SAMPLE_DURATION_SECONDS,
) -> GrainMeasurement | None:
    """None means "unknown" (unknown/non-positive duration, or every sample
    failed -- e.g. ffmpeg missing, corrupt stream) -- callers must not treat
    that as "zero grain", only as "grain could not be measured"."""
    if not duration or duration <= 0:
        return None
    path = Path(path)
    samples = [
        score
        for offset in _sample_offsets(duration, fractions, sample_duration)
        if (score := _measure_sample(path, offset, sample_duration)) is not None
    ]
    if not samples:
        return None
    return GrainMeasurement(score=sum(samples) / len(samples), samples=tuple(samples))
