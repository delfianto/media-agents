"""Orchestration: walks the library, probes each file, measures grain when it
could actually change the reported backend, and hands the results to
report.py to assemble/format. Read-only -- this skill never writes or
executes anything, only reports what transcode's own heuristics would do.
"""

import argparse
import json
import math
import sys

from psammophis.medialib import av1_backend
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.gpu import detect_av1_nvenc_gpu
from psammophis.medialib.grain import GRAIN_CPU_THRESHOLD, measure_grain
from psammophis.medialib.svt import detect_svt_implementation
from psammophis.medialib.videoprobe import probe_file
from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted, PhaseCompleted, PhaseStarted
from psammophis.runtime.roots import (
    RootError,
    resolve_default_root,
    root_option_source,
    validate_root,
)

from .report import analysis_to_dict, build_analysis, format_analysis

DEFAULT_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".ts", ".mov"})


def _grain_threshold(value: str) -> float:
    try:
        threshold = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise argparse.ArgumentTypeError("must be a finite number from 0 through 1")
    return threshold


def build_parser(default_root: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="psammophis analyze",
        description=(
            "Read-only: probe a video, measure its grain/noise level, and report exactly "
            "which transcode preset and backend (cpu/nvenc) its heuristics would pick "
            "and why -- the same decision transcode's own `probe`/`run` make, surfaced "
            "on its own so it doesn't require running transcode to see."
        ),
    )
    p.add_argument("--root", default=default_root, help="Media library root")
    p.add_argument("--path", help="Only consider files whose relative path contains this substring")
    p.add_argument("--limit", type=int, help="Stop after N files")
    p.add_argument(
        "--profile",
        choices=presets.PROFILES,
        default=presets.DEFAULT_PROFILE,
        help="Content profile for preset selection (default: film) -- see transcode's "
        "SKILL.md for why this is never auto-detected",
    )
    p.add_argument(
        "--grain-threshold",
        type=_grain_threshold,
        default=GRAIN_CPU_THRESHOLD,
        help=f"Grain/noise score at or above which the backend decision prefers cpu over "
        f"nvenc (default: {GRAIN_CPU_THRESHOLD} -- see medialib.grain for how it's measured "
        "and how provisional this default still is)",
    )
    p.add_argument(
        "--no-grain-routing",
        action="store_true",
        help="Don't measure per-file grain/noise -- report the pre-grain backend decision "
        "(nvenc whenever a GPU is available; DV/HDR10+ rules unchanged)",
    )
    p.add_argument(
        "--json", action="store_true", help="Print one JSON object per file instead of text"
    )
    return p


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    default = resolve_default_root()
    args = build_parser(str(default.path)).parse_args(raw)
    try:
        root = validate_root(args.root)
    except RootError as exc:
        print(f"Invalid media root: {exc}", file=sys.stderr)
        return 2

    candidates = list(
        walk_media_files(root, DEFAULT_EXTENSIONS, path_filter=args.path, limit=args.limit)
    )
    emitter = (
        context.start_run(
            command="analyze",
            root=root,
            root_source=root_option_source(raw, default),
            mode="read-only",
            items_total=len(candidates),
        )
        if context is not None
        else None
    )

    gpu_index = detect_av1_nvenc_gpu()
    nvencc_ok = av1_backend.nvencc_available()
    svt_implementation = detect_svt_implementation()

    results = []
    errors = 0
    for index, abs_path in enumerate(candidates, start=1):
        rel = abs_path.relative_to(root)
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(rel), index=index, total=len(candidates))
            emitter.emit(PhaseStarted, phase="probe", item=str(rel))
        try:
            probed = probe_file(abs_path)
        except Exception as exc:
            errors += 1
            if emitter is not None:
                emitter.emit(PhaseCompleted, phase="probe", item=str(rel), status="failed")
                if context is not None:
                    context.message(str(exc), level="error", item=str(rel), phase="probe")
                emitter.emit(ItemCompleted, item=str(rel), status="failed", detail=str(exc))
            else:
                print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
            continue
        if emitter is not None:
            emitter.emit(PhaseCompleted, phase="probe", item=str(rel), status="succeeded")
        if probed.get("video") is None:
            errors += 1
            detail = "no video stream found"
            if emitter is not None:
                if context is not None:
                    context.message(detail, level="error", item=str(rel))
                emitter.emit(ItemCompleted, item=str(rel), status="failed", detail=detail)
            else:
                print(f"  [ERROR] {rel}: {detail}", file=sys.stderr)
            continue

        grain = None
        if not args.no_grain_routing and av1_backend.grain_routing_applies(
            "auto", probed["video"], gpu_index, nvencc_ok
        ):
            if emitter is not None:
                emitter.emit(PhaseStarted, phase="measure-grain", item=str(rel))
            try:
                grain = measure_grain(abs_path, probed["format"].get("duration"))
            except Exception as exc:
                errors += 1
                if emitter is not None:
                    emitter.emit(
                        PhaseCompleted,
                        phase="measure-grain",
                        item=str(rel),
                        status="failed",
                    )
                    if context is not None:
                        context.message(
                            str(exc),
                            level="error",
                            item=str(rel),
                            phase="measure-grain",
                        )
                    emitter.emit(
                        ItemCompleted,
                        item=str(rel),
                        status="failed",
                        detail=str(exc),
                    )
                else:
                    print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
                continue
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="measure-grain",
                    item=str(rel),
                    status="succeeded",
                )

        analysis = build_analysis(
            str(rel),
            probed,
            args.profile,
            gpu_index,
            nvencc_ok,
            grain,
            svt_implementation,
            grain_threshold=args.grain_threshold,
        )
        results.append(analysis)
        if not args.json:
            print(format_analysis(analysis))
            print()
        if emitter is not None:
            emitter.emit(ItemCompleted, item=str(rel), status="succeeded")

    if args.json:
        print(json.dumps([analysis_to_dict(a) for a in results], indent=2))
    if context is not None:
        context.record_outcome(
            errors=errors,
            status="partial" if errors and results else ("failed" if errors else "succeeded"),
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
