"""Pure ffmpeg argv builders for lossless refs and variant encodes."""

from __future__ import annotations

from pathlib import Path

from psammophis.medialib.svt import SvtImplementation, implementation_params

from .spec import ClipWindow, ResolvedVariant


def extract_command(source: Path, destination: Path, window: ClipWindow) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{window.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{window.duration:.3f}",
        "-map",
        "0:v:0",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-an",
        "-sn",
        str(destination),
    ]


def _svt_params(
    variant: ResolvedVariant,
    implementation: SvtImplementation,
    color_transfer: str | None,
) -> str:
    if variant.tune == 5:
        params = {
            "enable-variance-boost": "1",
            "tune": "5",
            "adaptive-film-grain": "1",
            "variance-boost-curve": "3" if color_transfer == "smpte2084" else "0",
        }
    else:
        params = {
            "enable-variance-boost": "1",
            **implementation_params(implementation, color_transfer),
            "tune": str(variant.tune),
        }
    if variant.grain:
        params["film-grain"] = str(variant.grain)
        params["film-grain-denoise"] = "1" if variant.denoise else "0"
    return ":".join(f"{key}={value}" for key, value in params.items())


def encode_command(
    reference: Path,
    destination: Path,
    variant: ResolvedVariant,
    implementation: SvtImplementation,
    color_transfer: str | None,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(reference),
        "-map",
        "0:v:0",
        "-c:v",
        "libsvtav1",
        "-preset",
        str(variant.svt_preset),
        "-crf",
        str(variant.crf),
        "-pix_fmt",
        "yuv420p10le",
        "-svtav1-params",
        _svt_params(variant, implementation, color_transfer),
        "-an",
        str(destination),
    ]
