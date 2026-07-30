"""Pure report-shaping logic for one analyzed file -- no I/O, so this stays
unit-testable without mocking ffprobe/ffmpeg subprocess calls. cli.py does
the actual probing/grain-measuring/GPU-detecting and hands the results here
to assemble and format; nothing in this module reaches outside the dicts and
values it's given.
"""

from __future__ import annotations

from dataclasses import dataclass

from medialib import av1_backend, colorinfo
from medialib import av1_presets as presets
from medialib.grain import GrainMeasurement
from medialib.humansize import human_size


@dataclass(frozen=True)
class FileAnalysis:
    rel_path: str
    video: dict
    dynamic_range: str  # "SDR" | "HDR10" | "HDR10+" | "Dolby Vision"
    tier: str
    size: int | None
    preset: presets.Preset
    backend: str
    engine: str
    nvencc_ok: bool
    gpu_index: int | None
    grain: GrainMeasurement | None
    grain_threshold: float
    backend_error: str | None = None


def classify_dynamic_range(video: dict) -> str:
    if colorinfo.has_dolby_vision(video):
        return "Dolby Vision"
    if colorinfo.has_hdr10_plus(video):
        return "HDR10+"
    if colorinfo.is_hdr(video):
        return "HDR10"
    return "SDR"


def build_analysis(
    rel_path: str,
    probed: dict,
    profile: str,
    gpu_index: int | None,
    nvencc_ok: bool,
    grain: GrainMeasurement | None,
    grain_threshold: float = av1_backend.GRAIN_CPU_THRESHOLD,
) -> FileAnalysis:
    video = probed["video"]
    hdr = colorinfo.is_hdr(video)
    tier = presets.resolution_tier(video["height"])
    preset = presets.select_preset(video["height"], profile, hdr)
    grain_score = grain.score if grain is not None else None
    try:
        backend = av1_backend.choose_backend(
            video,
            "auto",
            gpu_index,
            nvencc_ok=nvencc_ok,
            grain_score=grain_score,
            grain_threshold=grain_threshold,
        )
        engine = av1_backend.choose_encode_engine(backend, video, nvencc_ok=nvencc_ok)
        backend_error = None
    except ValueError as exc:
        backend, engine, backend_error = "?", "?", str(exc)

    return FileAnalysis(
        rel_path=rel_path,
        video=video,
        dynamic_range=classify_dynamic_range(video),
        tier=tier,
        size=probed.get("format", {}).get("size"),
        preset=preset,
        backend=backend,
        engine=engine,
        nvencc_ok=nvencc_ok,
        gpu_index=gpu_index,
        grain=grain,
        grain_threshold=grain_threshold,
        backend_error=backend_error,
    )


def _grain_line(a: FileAnalysis) -> str | None:
    if a.grain is None:
        return None
    verdict = "cpu preferred" if a.grain.score >= a.grain_threshold else "clean, nvenc fine"
    samples = ", ".join(f"{s:.4f}" for s in a.grain.samples)
    return (
        f"grain: {a.grain.score:.4f} ({verdict}, threshold={a.grain_threshold:.4f}, "
        f"samples=[{samples}])"
    )


def format_analysis(a: FileAnalysis) -> str:
    video = a.video
    lines = [a.rel_path]
    lines.append(
        f"    {video['width']}x{video['height']} ({a.tier}) {video['codec_name']} "
        f"{video.get('profile') or ''} {a.dynamic_range}  size={human_size(a.size)}"
    )
    lines.append(f"    preset: {a.preset.name} -- {a.preset.description}")
    lines.append(
        f"    cpu:   preset={a.preset.svt_preset} crf={a.preset.crf} tune={a.preset.svt_tune} "
        f"film-grain={a.preset.film_grain} extra={a.preset.svt_extra}"
    )
    lines.append(
        f"    nvenc: preset={a.preset.nvenc_preset} tune={a.preset.nvenc_tune} "
        f"cq={a.preset.nvenc_cq} extra={a.preset.nvenc_extra}"
    )
    if a.backend_error:
        lines.append(f"    backend: ERROR -- {a.backend_error}")
    else:
        nvencc_label = "yes" if a.nvencc_ok else "no"
        gpu_label = str(a.gpu_index) if a.gpu_index is not None else "none"
        lines.append(
            f"    backend: {a.backend} via {a.engine}  "
            f"(gpu_index={gpu_label}, nvencc={nvencc_label})"
        )
    grain_line = _grain_line(a)
    if grain_line:
        lines.append(f"    {grain_line}")
    return "\n".join(lines)


def analysis_to_dict(a: FileAnalysis) -> dict:
    """JSON-serializable form for `--json` -- the same facts format_analysis
    prints, structured for scripting instead of eyeballing."""
    return {
        "path": a.rel_path,
        "width": a.video["width"],
        "height": a.video["height"],
        "resolution_tier": a.tier,
        "codec": a.video["codec_name"],
        "dynamic_range": a.dynamic_range,
        "size_bytes": a.size,
        "preset": a.preset.name,
        "backend": a.backend,
        "engine": a.engine,
        "backend_error": a.backend_error,
        "gpu_index": a.gpu_index,
        "nvencc_available": a.nvencc_ok,
        "grain_score": a.grain.score if a.grain is not None else None,
        "grain_samples": list(a.grain.samples) if a.grain is not None else None,
        "grain_threshold": a.grain_threshold,
    }
