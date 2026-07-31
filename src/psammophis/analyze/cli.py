"""Orchestration: walks the library, probes each file, measures grain when it
could actually change the reported backend, and hands the results to
report.py to assemble/format. Read-only -- this skill never writes or
executes anything, only reports what transcode's own heuristics would do.
"""

import argparse
import json
import sys
from pathlib import Path

from psammophis.medialib import av1_backend
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.gpu import detect_av1_nvenc_gpu
from psammophis.medialib.grain import GRAIN_CPU_THRESHOLD, measure_grain
from psammophis.medialib.svt import detect_svt_implementation
from psammophis.medialib.videoprobe import probe_file
from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.roots import resolve_default_root

from .report import analysis_to_dict, build_analysis, format_analysis

DEFAULT_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".ts", ".mov"})


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


def main(argv: list[str] | None = None) -> int:
    default_root = str(resolve_default_root().path)
    args = build_parser(default_root).parse_args(argv)
    root = Path(args.root)

    gpu_index = detect_av1_nvenc_gpu()
    nvencc_ok = av1_backend.nvencc_available()
    svt_implementation = detect_svt_implementation()

    results = []
    errors = 0
    for abs_path in walk_media_files(
        root, DEFAULT_EXTENSIONS, path_filter=args.path, limit=args.limit
    ):
        rel = abs_path.relative_to(root)
        try:
            probed = probe_file(abs_path)
        except Exception as exc:
            errors += 1
            print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
            continue
        if probed.get("video") is None:
            errors += 1
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
            svt_implementation,
            grain_threshold=args.grain_threshold,
        )
        results.append(analysis)
        if not args.json:
            print(format_analysis(analysis))
            print()

    if args.json:
        print(json.dumps([analysis_to_dict(a) for a in results], indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
