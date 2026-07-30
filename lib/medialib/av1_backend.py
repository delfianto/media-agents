"""Backend (`cpu`/`nvenc`) and encode-engine (`ffmpeg`/`nvencc`) selection for
AV1 encoding -- shared by av1-transcode's own `run`/`probe` and the read-only
`analyze` skill, so both report and apply the exact same decision instead of
two independently-drifting copies (see root AGENTS.md's `lib/medialib`
section for the bug class this is meant to avoid).
"""

from __future__ import annotations

import shutil

from . import colorinfo
from .grain import GRAIN_CPU_THRESHOLD


def nvencc_available() -> bool:
    return shutil.which("nvencc") is not None


def choose_backend(
    video: dict,
    requested: str,
    gpu_index: int | None,
    nvencc_ok: bool | None = None,
    grain_score: float | None = None,
    grain_threshold: float = GRAIN_CPU_THRESHOLD,
) -> str:
    """Resolve 'auto' into 'cpu' or 'nvenc'.

    Dolby Vision / HDR10+ cannot be preserved by plain ffmpeg `av1_nvenc`.
    When `nvencc` is available those sources stay on the GPU path (NVEncC
    attaches RPU / HDR10+). Without nvencc, DV falls back to CPU
    (libsvtav1 `-dolbyvision`); forcing `--backend nvenc` on DV without
    nvencc is an error rather than a silent metadata drop.

    `grain_score` (see grain.measure_grain) is an optional tie-breaker,
    checked only once DV/HDR10+ has already been ruled out as the deciding
    factor: a source measured at or above `grain_threshold` prefers cpu
    (libsvtav1's film-grain synthesis handles per-pixel noise far more
    efficiently than nvenc's AQ settings), even though a GPU is available.
    `grain_score=None` (not measured) leaves the GPU-if-available default
    from before this axis existed untouched -- grain can only ever push
    nvenc -> cpu, never the reverse, matching cpu's existing role as the
    safe/quality-preserving fallback throughout this module.
    """
    if nvencc_ok is None:
        nvencc_ok = nvencc_available()
    needs_dyn = colorinfo.needs_dynamic_metadata_path(video)

    if requested == "cpu":
        return "cpu"
    if requested == "nvenc":
        if gpu_index is None:
            raise ValueError("nvenc backend requested but no AV1-capable GPU is available")
        if colorinfo.has_dolby_vision(video) and not nvencc_ok:
            raise ValueError(
                "source has Dolby Vision metadata; plain ffmpeg av1_nvenc would drop it "
                "and nvencc was not found on PATH -- install nvencc (rigaya NVEnc) or "
                "pass --backend cpu (libsvtav1 preserves RPU via -dolbyvision)"
            )
        return "nvenc"
    # auto
    if gpu_index is None:
        return "cpu"
    if needs_dyn and colorinfo.has_dolby_vision(video) and not nvencc_ok:
        return "cpu"
    if grain_score is not None and grain_score >= grain_threshold:
        return "cpu"
    return "nvenc"


def choose_encode_engine(backend: str, video: dict, nvencc_ok: bool | None = None) -> str:
    """Return 'ffmpeg' or 'nvencc' for the concrete encode tool.

    Dynamic metadata on a GPU backend requires nvencc; everything else uses
    the existing ffmpeg builders (libsvtav1 or av1_nvenc).
    """
    if nvencc_ok is None:
        nvencc_ok = nvencc_available()
    if backend == "nvenc" and colorinfo.needs_dynamic_metadata_path(video):
        if not nvencc_ok:
            # HDR10+ without nvencc: fall through to ffmpeg (static HDR only).
            # DV without nvencc should never reach here for explicit nvenc
            # (choose_backend errors); auto already rewrote to cpu.
            return "ffmpeg"
        return "nvencc"
    return "ffmpeg"


def grain_routing_applies(
    requested: str, video: dict, gpu_index: int | None, nvencc_ok: bool
) -> bool:
    """True only when measuring grain could actually change choose_backend's
    result -- i.e. `requested == 'auto'`, a GPU is available, and Dolby
    Vision isn't already forcing cpu regardless of grain. Lets a caller skip
    the extra ffmpeg sampling passes (each a real decode, not free) when the
    answer is already decided without it."""
    if requested != "auto" or gpu_index is None:
        return False
    dv_forces_cpu = colorinfo.needs_dynamic_metadata_path(video) and colorinfo.has_dolby_vision(
        video
    )
    return not (dv_forces_cpu and not nvencc_ok)
