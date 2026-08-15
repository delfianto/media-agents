"""Reusable video comparison: aligned VMAF/PSNR/SSIM plus optional SSIMULACRA2."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

from .metrics import (
    check_libvmaf,
    extract_frame,
    find_ssimulacra2,
    find_vmaf_model,
    run_ssimulacra2,
    run_vmaf_clip,
)
from .probe import probe_video, validate_alignment
from .report import build_result
from .sampling import WORKLOADS, clip_ranges, stratified_timestamps

StepCallback = Callable[[str], None]
HeartbeatCallback = Callable[[], None]


def select_ssimulacra2(*, skip: bool, require: bool) -> tuple[str | None, str]:
    binary = None if skip else find_ssimulacra2()
    if require and binary is None:
        raise RuntimeError(
            "the official ssimulacra2 executable is unavailable; install libjxl dev tools"
        )
    if skip:
        return None, "skipped by request"
    if binary is None:
        return None, "official ssimulacra2 executable not found"
    return binary, "available"


def require_positive(name: str, value: int | float) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def compare_video_files(
    reference_path: Path,
    distorted_path: Path,
    work_dir: Path,
    *,
    mode: str = "deep",
    clips: int | None = None,
    clip_duration: float | None = None,
    ssimulacra_frames: int | None = None,
    threads: int = 8,
    skip_ssimulacra2: bool = False,
    require_ssimulacra2: bool = False,
    on_step: StepCallback | None = None,
    on_heartbeat: HeartbeatCallback | None = None,
) -> dict[str, object]:
    """Score distorted against reference. Never mutates either input."""
    if mode not in WORKLOADS:
        raise ValueError(f"unknown compare mode {mode!r}")
    check_libvmaf()
    reference = probe_video(reference_path)
    distorted = probe_video(distorted_path)
    alignment_errors = validate_alignment(reference, distorted)
    if alignment_errors:
        raise ValueError("alignment preflight failed:\n  - " + "\n  - ".join(alignment_errors))
    workload = WORKLOADS[mode]
    clip_count = clips if clips is not None else workload.clips
    duration = clip_duration if clip_duration is not None else workload.clip_duration
    ssim_count = ssimulacra_frames if ssimulacra_frames is not None else workload.ssimulacra_frames
    require_positive("clips", clip_count)
    require_positive("threads", threads)
    if not workload.full or clip_duration is not None:
        require_positive("clip duration", duration)
    if ssim_count < 0:
        raise ValueError("SSIMULACRA2 frame count cannot be negative")
    binary, initial_ssim_status = select_ssimulacra2(
        skip=skip_ssimulacra2, require=require_ssimulacra2
    )
    common_duration = min(reference.duration, distorted.duration)
    ranges = clip_ranges(common_duration, clip_count, duration)
    model, model_name = find_vmaf_model(reference.height)
    work_dir.mkdir(parents=True, exist_ok=True)
    vmaf_frames: list[dict[str, float]] = []
    ssimulacra_frames_out: list[dict[str, float]] = []
    for index, (start, span) in enumerate(ranges, start=1):
        if on_step is not None:
            on_step(f"VMAF {index}/{len(ranges)} at {start:.2f}s")
        vmaf_frames.extend(
            run_vmaf_clip(
                reference_path,
                distorted_path,
                start,
                span,
                model,
                threads,
                work_dir / f"vmaf-{index:03d}.json",
                reference.frame_rate,
                reference.width,
                reference.height,
                on_heartbeat=on_heartbeat,
            )
        )
    if binary and ssim_count:
        timestamps = stratified_timestamps(common_duration, ssim_count, margin=0.5)
        for index, timestamp in enumerate(timestamps, start=1):
            if on_step is not None:
                on_step(f"SSIMULACRA2 {index}/{len(timestamps)} at {timestamp:.2f}s")
            reference_png = work_dir / f"reference-{index:03d}.png"
            distorted_png = work_dir / f"distorted-{index:03d}.png"
            extract_frame(
                reference_path,
                timestamp,
                reference_png,
                reference,
                on_heartbeat=on_heartbeat,
            )
            extract_frame(
                distorted_path,
                timestamp,
                distorted_png,
                distorted,
                on_heartbeat=on_heartbeat,
            )
            ssimulacra_frames_out.append(
                {
                    "timestamp": timestamp,
                    "ssimulacra2": run_ssimulacra2(
                        binary,
                        reference_png,
                        distorted_png,
                        on_heartbeat=on_heartbeat,
                    ),
                }
            )
    if ssim_count == 0 and binary:
        ssim_status = "zero samples requested"
    elif binary is None:
        ssim_status = initial_ssim_status
    else:
        ssim_status = "measured"
    return build_result(
        reference,
        distorted,
        mode,
        model_name,
        ranges,
        vmaf_frames,
        ssimulacra_frames_out,
        ssim_status,
    )
