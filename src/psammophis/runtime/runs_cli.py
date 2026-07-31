"""Read-only inspection of durable run journals."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .context import AppContext
from .journal import (
    annotate_stale,
    default_state_root,
    journal_paths,
    list_runs,
    read_events,
    read_status,
)


def build_parser(default_state_dir: Path | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis runs",
        description="Inspect durable Psammophis run journals (read-only).",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=default_state_dir,
        help="Psammophis state directory (default: <root>/.cache/psammophis)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Media library root used to locate .cache/psammophis when --state-dir is omitted",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List known runs")
    list_p.add_argument("--active", action="store_true", help="Only running/stale runs")
    list_p.add_argument("--json", action="store_true", help="JSON array on stdout")

    show_p = sub.add_parser("show", help="Show one run's status")
    show_p.add_argument("run_id")
    show_p.add_argument("--json", action="store_true", default=True)

    events_p = sub.add_parser("events", help="Print journal events for a run")
    events_p.add_argument("run_id")
    events_p.add_argument("--after", type=int, default=0, help="Only events with seq > N")
    return parser


def _state_root(args: argparse.Namespace) -> Path:
    root = default_state_root(
        state_dir=args.state_dir,
        media_root=args.root,
        medialib_root=os.environ.get("MEDIALIB_ROOT"),
    )
    if root is None:
        raise SystemExit("no state directory: pass --state-dir or --root, or set MEDIALIB_ROOT")
    return root


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id not in (".", "..") and Path(run_id).name == run_id


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    args = build_parser(context.state_dir if context is not None else None).parse_args(argv)
    try:
        state_root = _state_root(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise

    if context is not None:
        context.start_run(
            command=f"runs {args.command}",
            root=state_root,
            root_source="--state-dir" if args.state_dir is not None else "runtime context",
            mode="read-only",
            use_root_for_state=False,
        )

    if args.command == "list":
        runs = [annotate_stale(r) for r in list_runs(state_root)]
        if args.active:
            runs = [r for r in runs if r.get("state") in ("running", "stale")]
        if args.json:
            print(json.dumps(runs, indent=2))
        else:
            if not runs:
                print("No runs found.")
            for run in runs:
                print(
                    f"{run.get('run_id')}  {run.get('state')}  "
                    f"{run.get('command')}  exit={run.get('exit_code')}"
                )
        return 0

    if not _valid_run_id(args.run_id):
        print(f"invalid run ID: {args.run_id!r}", file=sys.stderr)
        return 2
    paths = journal_paths(state_root, args.run_id)
    if not paths.status_path.is_file():
        print(f"unknown run: {args.run_id}", file=sys.stderr)
        return 1

    if args.command == "show":
        status = annotate_stale(read_status(paths.status_path))
        print(json.dumps(status, indent=2))
        return 0

    if args.command == "events":
        events = read_events(paths.events_path, after=args.after)
        for event in events:
            print(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
        return 0

    return 2
