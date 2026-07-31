import argparse
import sys
from pathlib import Path

from psammophis.medialib.tmdb import TmdbClient, TmdbError
from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted, PhaseCompleted, PhaseStarted
from psammophis.runtime.filesystem import RecoveryRequired
from psammophis.runtime.signals import CancellationRequested

from .config import ConfigError, load_config
from .parse import parse
from .plan import (
    VIDEO_EXTENSIONS,
    OrganizeResult,
    Plan,
    build_episode_plan,
    build_movie_plan,
    execute_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis organize", description="Identify and organize inbox media files"
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--inbox", help="Override ORGANIZE_INBOX_DIR")
    parser.add_argument("--path", help="Only paths containing this substring")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--yes", action="store_true", help="Apply the planned file operations")
    parser.add_argument("--copy", action="store_true", help="Copy instead of moving")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Back up and replace an existing destination",
    )
    return parser


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    tmdb = TmdbClient(cfg.tmdb_api_key, cfg.user_agent)
    inbox = Path(args.inbox or cfg.inbox_dir)
    if not inbox.is_dir():
        print(f"inbox is not a directory: {inbox}", file=sys.stderr)
        return 2
    counts = {"moved": 0, "planned": 0, "review": 0, "error": 0}
    files = list(
        walk_media_files(
            inbox,
            VIDEO_EXTENSIONS,
            path_filter=args.path,
            limit=args.limit,
            skip_root_files=False,
        )
    )
    emitter = (
        context.start_run(
            command="organize",
            root=inbox,
            root_source="--inbox" if args.inbox else "ORGANIZE_INBOX_DIR",
            mode="applied" if args.yes else "dry-run",
            items_total=len(files),
            wants_journal=args.yes,
            use_root_for_state=False,
        )
        if context is not None
        else None
    )
    for index, source in enumerate(files, start=1):
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(source), index=index, total=len(files))
            emitter.emit(PhaseStarted, phase="identify", item=str(source))
        result: Plan | OrganizeResult
        try:
            parsed = parse(source)
            if parsed is None:
                result = OrganizeResult(source, "error", "guessit could not parse filename")
            elif parsed.kind == "movie":
                result = build_movie_plan(cfg, tmdb, source)
            else:
                result = build_episode_plan(cfg, tmdb, source)
        except (OSError, TmdbError, ValueError) as exc:
            result = OrganizeResult(source, "error", str(exc))
        if isinstance(result, OrganizeResult):
            counts[result.status] += 1
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="identify",
                    item=str(source),
                    status="failed" if result.status == "error" else "succeeded",
                )
                emitter.emit(
                    ItemCompleted,
                    item=str(source),
                    status="failed" if result.status == "error" else "skipped",
                    detail=result.detail,
                )
            if result.status == "error" and context is not None:
                context.message(result.detail, level="error", item=str(source))
            else:
                stream = sys.stderr if result.status == "error" else sys.stdout
                print(f"[{result.status.upper()}] {result.source}: {result.detail}", file=stream)
            continue
        if emitter is not None:
            emitter.emit(PhaseCompleted, phase="identify", item=str(source), status="succeeded")
        if not args.yes:
            counts["planned"] += 1
            print(f"{result.source}\n    -> {result.video_path}")
            print(f"    confidence={result.confidence:.2f} ({result.match_reason})")
            if emitter is not None:
                emitter.emit(ItemCompleted, item=str(source), status="skipped", detail="planned")
            continue
        if emitter is not None:
            emitter.emit(PhaseStarted, phase="commit", item=str(source))
        try:
            applied = execute_plan(
                result,
                copy_instead_of_move=args.copy,
                overwrite=args.overwrite,
            )
        except KeyboardInterrupt, CancellationRequested:
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="commit",
                    item=str(source),
                    status="cancelled",
                )
                emitter.emit(ItemCompleted, item=str(source), status="cancelled")
            raise
        except RecoveryRequired as exc:
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="commit",
                    item=str(source),
                    status="failed",
                )
                emitter.emit(ItemCompleted, item=str(source), status="failed", detail=str(exc))
            raise
        counts[applied.status] += 1
        print(f"[{applied.status.upper()}] {applied.source}: {applied.detail}")
        if emitter is not None:
            failed = applied.status == "error"
            emitter.emit(
                PhaseCompleted,
                phase="commit",
                item=str(source),
                status="failed" if failed else "succeeded",
            )
            emitter.emit(
                ItemCompleted,
                item=str(source),
                status="failed" if failed else "succeeded",
                detail=applied.detail,
                output=None if failed else str(result.video_path),
            )
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] " + " ".join(f"{key}={value}" for key, value in counts.items()))
    if context is not None:
        context.record_outcome(
            changed=counts["moved"],
            planned=counts["planned"],
            errors=counts["error"],
            review=counts["review"],
            status=(
                "partial"
                if counts["error"] and (counts["moved"] or counts["planned"] or counts["review"])
                else "failed"
                if counts["error"]
                else "succeeded"
            ),
        )
    # review is intentional low-confidence holdout, not an operational failure
    return 1 if counts["error"] else 0
