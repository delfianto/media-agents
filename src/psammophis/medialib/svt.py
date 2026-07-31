"""Identify the SVT-AV1 implementation linked into FFmpeg.

FFmpeg exposes both upstream SVT-AV1 and juliobbv-p's ``svt-av1-hdr`` fork
through the same ``libsvtav1`` encoder name.  Treating their CRF scales and
defaults as interchangeable produced a very large real-world encode, so the
implementation is detected from the loaded library ABI rather than guessed
from FFmpeg's encoder name or a package name.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol, cast

SvtFlavor = Literal["mainline", "svt-av1-hdr", "unknown"]


@dataclass(frozen=True)
class SvtImplementation:
    flavor: SvtFlavor
    version: str | None
    library: str | None
    error: str | None = None

    @property
    def label(self) -> str:
        version = f" {self.version}" if self.version else ""
        return f"{self.flavor}{version}"


def implementation_params(
    implementation: SvtImplementation, color_transfer: str | None
) -> dict[str, str]:
    """Quality-sensitive parameters unique to the HDR fork."""
    if implementation.flavor != "svt-av1-hdr":
        return {}
    return {
        "sharpness": "1",
        "sharp-tx": "1",
        "tf-strength": "1",
        "kf-tf-strength": "1",
        "qp-scale-compress-strength": "1",
        "ac-bias": "1",
        "noise-norm-strength": "1",
        "adaptive-film-grain": "1",
        "noise-adaptive-filtering": "2",
        # Curve 3 is the fork's PQ-oriented curve. Keep curve 0 for
        # SDR/HLG instead of applying a PQ assumption to every HDR transfer.
        "variance-boost-curve": "3" if color_transfer == "smpte2084" else "0",
    }


_SVT_LDD_RE = re.compile(r"\blibSvtAv1Enc\.so(?:\.\d+)*\s+=>\s+(\S+)")


def _ffmpeg_svt_library() -> str | None:
    """Resolve the exact SVT shared object in FFmpeg's dependency tree."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None and shutil.which("ldd") is not None:
        try:
            proc = subprocess.run(
                ["ldd", ffmpeg],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            match = _SVT_LDD_RE.search(proc.stdout)
            if proc.returncode == 0 and match:
                return match.group(1)
        except OSError, subprocess.TimeoutExpired:
            pass
    return ctypes.util.find_library("SvtAv1Enc")


class _VersionFunction(Protocol):
    restype: object

    def __call__(self) -> bytes | None: ...


def _decode_version(function: _VersionFunction) -> str | None:
    function.restype = ctypes.c_char_p
    raw = function()
    return raw.decode("utf-8", errors="replace") if raw else None


@lru_cache(maxsize=1)
def detect_svt_implementation() -> SvtImplementation:
    """Detect the system libSvtAv1Enc implementation without running an encode.

    ``svt-av1-hdr`` deliberately preserves upstream's library/encoder names,
    but adds the ``svt_hdr_get_version`` ABI symbol.  Looking for that symbol
    in the exact shared object resolved in FFmpeg's dependency tree and reading
    ``svt_av1_get_version`` gives us a cheap, read-only discriminator.
    """

    library = _ffmpeg_svt_library()
    if library is None:
        return SvtImplementation("unknown", None, None, "libSvtAv1Enc was not found")
    try:
        loaded = ctypes.CDLL(library)
        version_fn = getattr(loaded, "svt_av1_get_version", None)
        version = (
            _decode_version(cast(_VersionFunction, version_fn)) if version_fn is not None else None
        )
        flavor: SvtFlavor = "svt-av1-hdr" if hasattr(loaded, "svt_hdr_get_version") else "mainline"
        return SvtImplementation(flavor, version, library)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        return SvtImplementation("unknown", None, library, str(exc))
