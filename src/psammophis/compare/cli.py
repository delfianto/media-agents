import argparse
import json
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
from .report import format_report
from .sampling import WORKLOADS
from .video import compare_video_files, require_positive, select_ssimulacra2


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
    require_positive(name, value)


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
    return select_ssimulacra2(skip=args.skip_ssimulacra2, require=args.require_ssimulacra2)


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
            emitter.emit(PhaseStarted, phase="compare-video", item=item_name)
        active_phase = "compare-video"
        active_phase_started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="compare-") as temp_name:

            def on_step(text: str) -> None:
                if context is None:
                    print(f"[{text}]", file=sys.stderr, flush=True)
                if emitter is not None:
                    emitter.emit(ItemProgress, item=item_name, phase="compare-video")

            result = compare_video_files(
                reference_path,
                distorted_path,
                Path(temp_name),
                mode=args.mode,
                clips=args.clips,
                clip_duration=args.clip_duration,
                ssimulacra_frames=args.ssimulacra_frames,
                threads=args.threads,
                skip_ssimulacra2=args.skip_ssimulacra2,
                require_ssimulacra2=args.require_ssimulacra2,
                on_step=on_step,
                on_heartbeat=(lambda: emit_heartbeat("compare-video"))
                if emitter is not None
                else None,
            )
        if emitter is not None:
            emitter.emit(
                PhaseCompleted,
                phase="compare-video",
                item=item_name,
                status="succeeded",
                elapsed_seconds=time.monotonic() - active_phase_started,
            )
        active_phase = None
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
