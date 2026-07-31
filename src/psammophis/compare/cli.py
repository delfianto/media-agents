import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reference_path = Path(args.reference).expanduser().resolve()
    distorted_path = Path(args.distorted).expanduser().resolve()
    try:
        if not reference_path.is_file():
            raise ValueError(f"reference is not a file: {reference_path}")
        if not distorted_path.is_file():
            raise ValueError(f"distorted input is not a file: {distorted_path}")
        if reference_path.samefile(distorted_path):
            raise ValueError("reference and distorted inputs are the same file")
        media_type = _media_type(args, reference_path, distorted_path)
        binary, initial_ssim_status = _ssimulacra_selection(args)
        if media_type == "image":
            with tempfile.TemporaryDirectory(prefix="compare-image-") as temp_name:
                result = compare_images(
                    reference_path,
                    distorted_path,
                    Path(temp_name),
                    binary,
                    initial_ssim_status,
                )
            encoded = json.dumps(result, indent=2)
            if args.json_out:
                args.json_out.parent.mkdir(parents=True, exist_ok=True)
                args.json_out.write_text(encoded + "\n")
            print(encoded if args.json else format_image_report(result))
            if args.json_out and not args.json:
                print(f"\nFull JSON: {args.json_out}")
            return 0

        check_libvmaf()
        reference = probe_video(reference_path)
        distorted = probe_video(distorted_path)
        alignment_errors = validate_alignment(reference, distorted)
        if alignment_errors:
            raise ValueError("alignment preflight failed:\n  - " + "\n  - ".join(alignment_errors))

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
            for index, (start, duration) in enumerate(ranges, start=1):
                print(
                    f"[VMAF {index}/{len(ranges)}] {start:.2f}s for {duration:.2f}s",
                    file=sys.stderr,
                    flush=True,
                )
                frames = run_vmaf_clip(
                    reference_path,
                    distorted_path,
                    start,
                    duration,
                    model,
                    args.threads,
                    temp / f"vmaf-{index:03d}.json",
                    reference.frame_rate,
                )
                vmaf_frames.extend(frames)
            if binary and ssim_count:
                timestamps = stratified_timestamps(common_duration, ssim_count, margin=0.5)
                for index, timestamp in enumerate(timestamps, start=1):
                    print(
                        f"[SSIMULACRA2 {index}/{len(timestamps)}] {timestamp:.2f}s",
                        file=sys.stderr,
                        flush=True,
                    )
                    reference_png = temp / f"reference-{index:03d}.png"
                    distorted_png = temp / f"distorted-{index:03d}.png"
                    extract_frame(reference_path, timestamp, reference_png, reference)
                    extract_frame(distorted_path, timestamp, distorted_png, distorted)
                    ssimulacra_frames.append(
                        {
                            "timestamp": timestamp,
                            "ssimulacra2": run_ssimulacra2(binary, reference_png, distorted_png),
                        }
                    )

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
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(encoded + "\n")
        print(encoded if args.json else format_report(result))
        if args.json_out and not args.json:
            print(f"\nFull JSON: {args.json_out}")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
