"""Orchestration: walks the library, probes each file, measures grain when it
could actually change the reported backend, and hands the results to
report.py to assemble/format. Read-only -- this skill never writes or
executes anything, only reports what av1-transcode's own heuristics would do.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from medialib import av1_backend
from medialib import av1_presets as presets
from medialib.gpu import detect_av1_nvenc_gpu
from medialib.grain import GRAIN_CPU_THRESHOLD, measure_grain
from medialib.libroot import find_library_root, find_own_script_path
from medialib.videoprobe import probe_file
from medialib.walk import walk_media_files

from .report import analysis_to_dict, build_analysis, format_analysis

DEFAULT_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".ts", ".mov"})


def build_parser(default_root: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze",
        description=(
            "Read-only: probe a video, measure its grain/noise level, and report exactly "
            "which av1-transcode preset and backend (cpu/nvenc) its heuristics would pick "
            "and why -- the same decision av1-transcode's own `probe`/`run` make, surfaced "
            "on its own so it doesn't require running av1-transcode to see."
        ),
    )
    p.add_argument("--root", default=default_root, help="Media library root")
    p.add_argument("--path", help="Only consider files whose relative path contains this substring")
    p.add_argument("--limit", type=int, help="Stop after N files")
    p.add_argument(
        "--profile",
        choices=presets.PROFILES,
        default=presets.DEFAULT_PROFILE,
        help="Content profile for preset selection (default: film) -- see av1-transcode's "
        "SKILL.md for why this is never auto-detected",
    )
    p.add_argument(
        "--grain-threshold",
        type=float,
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


def main(argv: list[str] | None = None) -> None:
    try:
        default_root = os.environ.get("MEDIALIB_ROOT") or str(
            find_library_root(find_own_script_path(__file__))
        )
    except RuntimeError as exc:
        print(f"{exc}. Pass --root explicitly, or set MEDIALIB_ROOT.", file=sys.stderr)
        sys.exit(1)

    args = build_parser(default_root).parse_args(argv)
    root = Path(args.root)

    gpu_index = detect_av1_nvenc_gpu()
    nvencc_ok = av1_backend.nvencc_available()

    results = []
    for abs_path in walk_media_files(
        root, DEFAULT_EXTENSIONS, path_filter=args.path, limit=args.limit
    ):
        rel = abs_path.relative_to(root)
        try:
            probed = probe_file(abs_path)
        except Exception as exc:
            print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
            continue
        if probed.get("video") is None:
            print(f"  [ERROR] {rel}: no video stream found", file=sys.stderr)
            continue

        grain = None
        if not args.no_grain_routing and av1_backend.grain_routing_applies(
            "auto", probed["video"], gpu_index, nvencc_ok
        ):
            grain = measure_grain(abs_path, probed["format"].get("duration"))

        analysis = build_analysis(
            str(rel),
            probed,
            args.profile,
            gpu_index,
            nvencc_ok,
            grain,
            grain_threshold=args.grain_threshold,
        )
        results.append(analysis)
        if not args.json:
            print(format_analysis(analysis))
            print()

    if args.json:
        print(json.dumps([analysis_to_dict(a) for a in results], indent=2))


if __name__ == "__main__":
    main()
