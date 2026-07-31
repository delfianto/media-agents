import argparse
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

from psammophis.runtime.context import AppContext
from psammophis.runtime.events import (
    ItemCompleted,
    ItemProgress,
    ItemStarted,
    PhaseCompleted,
    PhaseStarted,
    RunHeartbeat,
)
from psammophis.runtime.filesystem import atomic_write_text
from psammophis.runtime.signals import CancellationRequested

from .image import IMAGE_EXTENSIONS, compare_images, format_image_report
from .metrics import (
    check_libvmaf,
    extract_frame,
    find_ssimulacra2,
    find_vmaf_model,
    run_ssimulacra2,
    run_vmaf_clip,
)
from .probe import probe_video, validate_alignment
from .report import build_result, format_report
from .sampling import WORKLOADS, clip_ranges, stratified_timestamps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis compare",
        description=(
            "Deep, read-only source-versus-encode comparison: aligned VMAF/PSNR/"
            "SSIM for video, color-managed SSIM/PSNR/RMSE for images, and optional "
            "SSIMULACRA2 for both."
        ),
    )
    parser.add_argument("--reference", required=True, help="Original/reference media file")
    parser.add_argument("--distorted", required=True, help="Encoded/distorted media file")
    parser.add_argument(
        "--media-type",
        choices=("auto", "video", "image"),
        default="auto",
        help="Comparison type (default: infer from both file extensions)",
    )
    parser.add_argument("--mode", choices=WORKLOADS, default="deep")
    parser.add_argument("--clips", type=int, help="Override the number of stratified VMAF clips")
    parser.add_argument(
        "--clip-duration", type=float, help="Override each VMAF clip duration in seconds"
    )
    parser.add_argument(
        "--ssimulacra-frames",
        type=int,
        help="Override the number of stratified SSIMULACRA2 still frames",
    )
    parser.add_argument(
        "--skip-ssimulacra2", action="store_true", help="Skip SSIMULACRA2 even if installed"
    )
    parser.add_argument(
        "--require-ssimulacra2",
        action="store_true",
        help="Fail instead of continuing when the official executable is unavailable",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=max(1, min(16, os.cpu_count() or 1)),
        help="libvmaf worker threads (default: min(16, CPU count))",
    )
    parser.add_argument("--json-out", type=Path, help="Write full per-frame JSON to this path")
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of text")
    return parser


def _positive(name: str, value: int | float) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _media_type(args: argparse.Namespace, reference: Path, distorted: Path) -> str:
    if args.media_type != "auto":
        return args.media_type
    reference_is_image = reference.suffix.lower() in IMAGE_EXTENSIONS
    distorted_is_image = distorted.suffix.lower() in IMAGE_EXTENSIONS
    if reference_is_image and distorted_is_image:
        return "image"
    if reference_is_image != distorted_is_image:
        raise ValueError(
            "one input looks like an image and the other does not; pass --media-type explicitly"
        )
    return "video"


def _ssimulacra_selection(args: argparse.Namespace) -> tuple[str | None, str]:
    binary = None if args.skip_ssimulacra2 else find_ssimulacra2()
    if args.require_ssimulacra2 and binary is None:
        raise RuntimeError(
            "the official ssimulacra2 executable is unavailable; install libjxl dev tools"
        )
    if args.skip_ssimulacra2:
        return None, "skipped by request"
    if binary is None:
        return None, "official ssimulacra2 executable not found"
    return binary, "available"


def _validate_json_destination(
    destination: Path | None,
    reference: Path,
    distorted: Path,
) -> None:
    if destination is None:
        return
    resolved = destination.expanduser().resolve(strict=False)
    if resolved in (reference, distorted):
        raise ValueError("--json-out must not overwrite either media input")


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    args = build_parser().parse_args(argv)
    reference_path = Path(args.reference).expanduser().resolve()
    distorted_path = Path(args.distorted).expanduser().resolve()
    json_out = args.json_out.expanduser().resolve(strict=False) if args.json_out else None
    item_name = f"{reference_path.name} vs {distorted_path.name}"
    emitter = (
        context.start_run(
            command="compare",
            root=reference_path,
            root_source="--reference",
            mode="read-only",
            items_total=1,
            wants_journal=True,
            use_root_for_state=False,
        )
        if context is not None
        else None
    )
    if emitter is not None:
        emitter.emit(ItemStarted, item=item_name, index=1, total=1)
        emitter.emit(PhaseStarted, phase="preflight", item=item_name)
    preflight_started = time.monotonic()
    active_phase: str | None = "preflight"
    active_phase_started = preflight_started

    def emit_heartbeat(phase: str) -> None:
        if emitter is not None:
            emitter.emit(
                RunHeartbeat,
                phase=phase,
                item=item_name,
                message="still running",
            )

    try:
        if not reference_path.is_file():
            raise ValueError(f"reference is not a file: {reference_path}")
        if not distorted_path.is_file():
            raise ValueError(f"distorted input is not a file: {distorted_path}")
        if reference_path.samefile(distorted_path):
            raise ValueError("reference and distorted inputs are the same file")
        _validate_json_destination(json_out, reference_path, distorted_path)
        media_type = _media_type(args, reference_path, distorted_path)
        binary, initial_ssim_status = _ssimulacra_selection(args)
        if emitter is not None:
            emitter.emit(
                PhaseCompleted,
                phase="preflight",
                item=item_name,
                status="succeeded",
                elapsed_seconds=time.monotonic() - preflight_started,
            )
        active_phase = None
        if media_type == "image":
            if emitter is not None:
                emitter.emit(PhaseStarted, phase="compare-image", item=item_name)
            phase_started = time.monotonic()
            active_phase = "compare-image"
            active_phase_started = phase_started
            with tempfile.TemporaryDirectory(prefix="compare-image-") as temp_name:
                result = compare_images(
                    reference_path,
                    distorted_path,
                    Path(temp_name),
                    binary,
                    initial_ssim_status,
                    on_heartbeat=(lambda: emit_heartbeat("compare-image")),
                )
            encoded = json.dumps(result, indent=2)
            if json_out:
                json_out.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(json_out, encoded + "\n")
            print(encoded if args.json else format_image_report(result))
            if json_out and not args.json:
                print(f"\nFull JSON: {json_out}")
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="compare-image",
                    item=item_name,
                    status="succeeded",
                    elapsed_seconds=time.monotonic() - phase_started,
                )
                emitter.emit(ItemCompleted, item=item_name, status="succeeded")
            active_phase = None
            if context is not None:
                context.record_outcome(status="succeeded")
            return 0

        if emitter is not None:
            emitter.emit(PhaseStarted, phase="probe", item=item_name)
        active_phase = "probe"
        active_phase_started = time.monotonic()
        check_libvmaf()
        reference = probe_video(reference_path)
        distorted = probe_video(distorted_path)
        alignment_errors = validate_alignment(reference, distorted)
        if alignment_errors:
            raise ValueError("alignment preflight failed:\n  - " + "\n  - ".join(alignment_errors))
        if emitter is not None:
            emitter.emit(
                PhaseCompleted,
                phase="probe",
                item=item_name,
                status="succeeded",
                elapsed_seconds=time.monotonic() - active_phase_started,
            )
        active_phase = None

        workload = WORKLOADS[args.mode]
        clips = args.clips if args.clips is not None else workload.clips
        clip_duration = (
            args.clip_duration if args.clip_duration is not None else workload.clip_duration
        )
        ssim_count = (
            args.ssimulacra_frames
            if args.ssimulacra_frames is not None
            else workload.ssimulacra_frames
        )
        _positive("clips", clips)
        _positive("threads", args.threads)
        if not workload.full or args.clip_duration is not None:
            _positive("clip duration", clip_duration)
        if ssim_count < 0:
            raise ValueError("SSIMULACRA2 frame count cannot be negative")
        common_duration = min(reference.duration, distorted.duration)
        ranges = clip_ranges(common_duration, clips, clip_duration)
        model, model_name = find_vmaf_model(reference.height)
        vmaf_frames: list[dict[str, float]] = []
        ssimulacra_frames: list[dict[str, float]] = []
        with tempfile.TemporaryDirectory(prefix="compare-") as temp_name:
            temp = Path(temp_name)
            total_steps = len(ranges) + (ssim_count if binary else 0)
            completed_steps = 0
            if emitter is not None:
                emitter.emit(PhaseStarted, phase="vmaf", item=item_name)
            vmaf_started = time.monotonic()
            active_phase = "vmaf"
            active_phase_started = vmaf_started
            for index, (start, duration) in enumerate(ranges, start=1):
                progress_text = f"VMAF {index}/{len(ranges)} at {start:.2f}s"
                if context is None:
                    print(f"[{progress_text}]", file=sys.stderr, flush=True)
                frames = run_vmaf_clip(
                    reference_path,
                    distorted_path,
                    start,
                    duration,
                    model,
                    args.threads,
                    temp / f"vmaf-{index:03d}.json",
                    reference.frame_rate,
                    on_heartbeat=(lambda: emit_heartbeat("vmaf")) if emitter is not None else None,
                )
                vmaf_frames.extend(frames)
                completed_steps += 1
                if emitter is not None:
                    emitter.emit(
                        ItemProgress,
                        item=item_name,
                        phase="vmaf",
                        percent=(completed_steps / total_steps * 100.0) if total_steps else 100.0,
                    )
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="vmaf",
                    item=item_name,
                    status="succeeded",
                    elapsed_seconds=time.monotonic() - vmaf_started,
                )
            active_phase = None
            if binary and ssim_count:
                if emitter is not None:
                    emitter.emit(PhaseStarted, phase="ssimulacra2", item=item_name)
                ssim_started = time.monotonic()
                active_phase = "ssimulacra2"
                active_phase_started = ssim_started
                timestamps = stratified_timestamps(common_duration, ssim_count, margin=0.5)
                for index, timestamp in enumerate(timestamps, start=1):
                    progress_text = f"SSIMULACRA2 {index}/{len(timestamps)} at {timestamp:.2f}s"
                    if context is None:
                        print(f"[{progress_text}]", file=sys.stderr, flush=True)
                    reference_png = temp / f"reference-{index:03d}.png"
                    distorted_png = temp / f"distorted-{index:03d}.png"

                    def heartbeat() -> None:
                        emit_heartbeat("ssimulacra2")

                    extract_frame(
                        reference_path,
                        timestamp,
                        reference_png,
                        reference,
                        on_heartbeat=heartbeat,
                    )
                    extract_frame(
                        distorted_path,
                        timestamp,
                        distorted_png,
                        distorted,
                        on_heartbeat=heartbeat,
                    )
                    ssimulacra_frames.append(
                        {
                            "timestamp": timestamp,
                            "ssimulacra2": run_ssimulacra2(
                                binary,
                                reference_png,
                                distorted_png,
                                on_heartbeat=heartbeat,
                            ),
                        }
                    )
                    completed_steps += 1
                    if emitter is not None:
                        emitter.emit(
                            ItemProgress,
                            item=item_name,
                            phase="ssimulacra2",
                            percent=(completed_steps / total_steps * 100.0),
                        )
                if emitter is not None:
                    emitter.emit(
                        PhaseCompleted,
                        phase="ssimulacra2",
                        item=item_name,
                        status="succeeded",
                        elapsed_seconds=time.monotonic() - ssim_started,
                    )
                active_phase = None

        if ssim_count == 0 and binary:
            ssim_status = "zero samples requested"
        elif binary is None:
            ssim_status = initial_ssim_status
        else:
            ssim_status = "measured"
        result = build_result(
            reference,
            distorted,
            args.mode,
            model_name,
            ranges,
            vmaf_frames,
            ssimulacra_frames,
            ssim_status,
        )
        encoded = json.dumps(result, indent=2)
        if json_out:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(json_out, encoded + "\n")
        print(encoded if args.json else format_report(result))
        if json_out and not args.json:
            print(f"\nFull JSON: {json_out}")
    except KeyboardInterrupt, CancellationRequested:
        if emitter is not None:
            if active_phase is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase=active_phase,
                    item=item_name,
                    status="cancelled",
                    elapsed_seconds=time.monotonic() - active_phase_started,
                )
            emitter.emit(ItemCompleted, item=item_name, status="cancelled")
        raise
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        if context is not None:
            context.message(str(exc), level="error", item=item_name)
            context.record_outcome(status="failed", errors=1)
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        if emitter is not None:
            if active_phase is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase=active_phase,
                    item=item_name,
                    status="failed",
                    elapsed_seconds=time.monotonic() - active_phase_started,
                )
            emitter.emit(ItemCompleted, item=item_name, status="failed", detail=str(exc))
        return 1
    if emitter is not None:
        emitter.emit(ItemCompleted, item=item_name, status="succeeded")
    if context is not None:
        context.record_outcome(status="succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
