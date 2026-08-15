"""CLI for clip-aligned encoder A/B tests."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from psammophis.compare.probe import probe_video
from psammophis.compare.sampling import WORKLOADS
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.svt import detect_svt_implementation
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted, PhaseCompleted, PhaseStarted
from psammophis.runtime.roots import (
    RootError,
    resolve_default_root,
    root_option_source,
    validate_root,
)
from psammophis.runtime.signals import CancellationRequested

from .run import format_plan, render_report, run_experiment
from .spec import parse_starts, parse_variant, plan_windows, resolve_variant


def build_parser(default_root: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis abtest",
        description=(
            "Clip-aligned encoder A/B: extract lossless references, encode each "
            "clip with two or more libsvtav1 recipes, and score them with compare. "
            "Never mutates the source. Defaults to a dry-run plan."
        ),
    )
    parser.add_argument("--root", default=default_root, help="Media library root")
    parser.add_argument("--source", required=True, help="Original video to sample")
    parser.add_argument(
        "--variant",
        action="append",
        dest="variants",
        required=True,
        metavar="SPEC",
        help=(
            "Repeatable. id=2160p-film or id=2160p-film,crf=26,grain=0 or "
            "id=crf=26,preset=5,tune=0,grain=0,denoise=0"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=presets.PROFILES,
        default=presets.DEFAULT_PROFILE,
        help="Base table profile when a variant does not name a preset (default: film)",
    )
    parser.add_argument(
        "--at",
        dest="starts",
        help="Comma-separated clip start times in seconds (overrides stratified --clips)",
    )
    parser.add_argument(
        "--clips",
        type=int,
        default=6,
        help="Number of stratified clips when --at is omitted (default: 6)",
    )
    parser.add_argument(
        "--clip-duration",
        type=float,
        default=20.0,
        help="Seconds per clip (default: 20)",
    )
    parser.add_argument(
        "--compare-mode",
        choices=tuple(WORKLOADS),
        default="full",
        help="compare workload for each clip pair (default: full)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="libvmaf worker threads (default: 8)",
    )
    parser.add_argument(
        "--skip-ssimulacra2",
        action="store_true",
        help="Skip SSIMULACRA2 even if installed",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for refs/encodes/reports (default under <root>/.cache/psammophis/abtest/)",
    )
    parser.add_argument("--json", action="store_true", help="Print rollup JSON instead of a table")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Execute extracts, encodes, and compares (default is dry-run)",
    )
    return parser


def _default_output_dir(root: Path, source: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / ".cache" / "psammophis" / "abtest" / f"{source.stem}-{stamp}"


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    default = resolve_default_root()
    args = build_parser(str(default.path)).parse_args(raw)
    try:
        root = validate_root(Path(args.root))
    except RootError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        return 2
    candidate = Path(args.source).expanduser()
    source = candidate.resolve() if candidate.is_file() else (root / args.source).resolve()
    if not source.is_file():
        print(f"[ERROR] source is not a file: {args.source}", file=sys.stderr, flush=True)
        return 2
    if len(args.variants) < 2:
        print("[ERROR] need at least two --variant specs", file=sys.stderr, flush=True)
        return 2
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else _default_output_dir(root, source)
    )
    if output_dir.resolve() in {source, source.parent}:
        print(
            "[ERROR] --output-dir must not be the source file or its directory",
            file=sys.stderr,
            flush=True,
        )
        return 2

    emitter = None
    if context is not None:
        emitter = context.start_run(
            command="abtest",
            root=root,
            root_source=root_option_source(raw or [], default),
            mode="read-only" if not args.yes else "applied",
            items_total=1,
            wants_journal=True,
            use_root_for_state=True,
        )
        emitter.emit(ItemStarted, item=source.name, index=1, total=1)

    try:
        parsed = [parse_variant(raw) for raw in args.variants]
        info = probe_video(source)
        implementation = detect_svt_implementation()
        resolved = [
            resolve_variant(item, info.height, args.profile, implementation) for item in parsed
        ]
        starts = parse_starts(args.starts) if args.starts else None
        windows = plan_windows(
            info.duration,
            starts=starts,
            clip_count=args.clips,
            clip_duration=args.clip_duration,
        )
        if not args.yes:
            print(
                format_plan(
                    source,
                    output_dir,
                    windows,
                    resolved,
                    args.compare_mode,
                    implementation,
                    info.color_transfer,
                )
            )
            if emitter is not None:
                emitter.emit(ItemCompleted, item=source.name, status="succeeded")
            if context is not None:
                context.record_outcome(status="succeeded", planned=len(windows) * len(resolved))
            return 0
        if emitter is not None:
            emitter.emit(PhaseStarted, phase="experiment", item=source.name)

        def heartbeat(phase: str) -> None:
            if context is not None and context.emitter is not None:
                from psammophis.runtime.events import RunHeartbeat

                context.emitter.emit(RunHeartbeat, phase=phase, item=source.name)

        scores, summaries = run_experiment(
            source,
            output_dir,
            windows,
            resolved,
            implementation,
            info.color_transfer,
            compare_mode=args.compare_mode,
            threads=args.threads,
            skip_ssimulacra2=args.skip_ssimulacra2,
            heartbeat=heartbeat,
        )
        if emitter is not None:
            emitter.emit(PhaseCompleted, phase="experiment", item=source.name, status="succeeded")
        if args.json:
            from .report import scores_to_jsonable

            print(json.dumps(scores_to_jsonable(scores, summaries), indent=2))
        else:
            print(render_report(source, scores, summaries))
            print(f"\nRollup: {output_dir / 'rollup.json'}")
        if emitter is not None:
            emitter.emit(ItemCompleted, item=source.name, status="succeeded")
        if context is not None:
            context.record_outcome(status="succeeded", changed=len(scores))
        return 0
    except KeyboardInterrupt, CancellationRequested:
        if emitter is not None:
            emitter.emit(ItemCompleted, item=source.name, status="cancelled")
        raise
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        if emitter is not None:
            emitter.emit(ItemCompleted, item=source.name, status="failed", detail=str(exc))
        if context is not None:
            context.record_outcome(status="failed", errors=1)
        return 1
