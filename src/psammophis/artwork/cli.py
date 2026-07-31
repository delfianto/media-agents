import argparse
import sys
from pathlib import Path

from psammophis.medialib.tmdb import TmdbClient, TmdbError
from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted, PhaseCompleted, PhaseStarted

from .config import ConfigError, load_config
from .core import ARTWORK_TYPES, VIDEO_EXTENSIONS, ArtworkError, build_plan, execute, identify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis artwork", description="Fetch artwork and NFO metadata"
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--path", required=True, help="An organized video file or directory")
    parser.add_argument("--type", choices=ARTWORK_TYPES, default="all")
    parser.add_argument("--tmdb-id", type=int, help="Override a missing provider tag for one file")
    parser.add_argument("--filter", help="Only relative paths containing this substring")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    target = Path(args.path)
    if not target.exists():
        print(f"path does not exist: {target}", file=sys.stderr)
        return 2
    if args.tmdb_id and target.is_dir():
        print("--tmdb-id is only valid when --path names one video file", file=sys.stderr)
        return 2
    files = list(
        [target]
        if target.is_file()
        else walk_media_files(
            target,
            VIDEO_EXTENSIONS,
            path_filter=args.filter,
            limit=args.limit,
            skip_root_files=False,
        )
    )
    emitter = (
        context.start_run(
            command="artwork",
            root=target,
            root_source="--path",
            mode="applied" if args.yes else "dry-run",
            items_total=len(files),
            wants_journal=args.yes,
            use_root_for_state=False,
        )
        if context is not None
        else None
    )
    tmdb = TmdbClient(cfg.tmdb_api_key, cfg.user_agent)
    errors = changed = planned = 0
    for index, video in enumerate(files, start=1):
        item_changed = False
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(video), index=index, total=len(files))
            emitter.emit(PhaseStarted, phase="identify", item=str(video))
        try:
            writes = build_plan(identify(video, args.tmdb_id), tmdb, args.type)
        except (ArtworkError, OSError, TmdbError, ValueError) as exc:
            errors += 1
            if emitter is not None:
                emitter.emit(PhaseCompleted, phase="identify", item=str(video), status="failed")
                if context is not None:
                    context.message(str(exc), level="error", item=str(video))
                emitter.emit(ItemCompleted, item=str(video), status="failed", detail=str(exc))
            else:
                print(f"[ERROR] {exc}", file=sys.stderr)
            continue
        if emitter is not None:
            emitter.emit(PhaseCompleted, phase="identify", item=str(video), status="succeeded")
        print(video)
        for write in writes:
            print(f"    {write.kind}: {write.destination}")
        if args.yes:
            if emitter is not None:
                emitter.emit(PhaseStarted, phase="download", item=str(video))
            try:
                warnings = execute(writes, tmdb)
            except OSError as exc:
                errors += 1
                if emitter is not None:
                    emitter.emit(
                        PhaseCompleted,
                        phase="download",
                        item=str(video),
                        status="failed",
                    )
                if context is not None:
                    context.message(str(exc), level="error", item=str(video), phase="download")
                else:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                if emitter is not None:
                    emitter.emit(ItemCompleted, item=str(video), status="failed", detail=str(exc))
                continue
            for warning in warnings:
                if context is not None:
                    context.message(warning, level="warning", item=str(video), phase="download")
                else:
                    print(f"[WARNING] {warning}", file=sys.stderr)
            if len(warnings) < len(writes):
                changed += 1
                item_changed = True
            if emitter is not None:
                emitter.emit(PhaseCompleted, phase="download", item=str(video), status="succeeded")
        elif writes:
            planned += 1
        if emitter is not None:
            emitter.emit(
                ItemCompleted,
                item=str(video),
                status="succeeded" if item_changed else "skipped",
                detail=(
                    None
                    if item_changed
                    else "planned"
                    if not args.yes and writes
                    else "no artwork written"
                ),
            )
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} errors={errors}")
    if context is not None:
        context.record_outcome(
            changed=changed,
            planned=planned,
            errors=errors,
            status=(
                "partial"
                if errors and (changed or planned)
                else "failed"
                if errors
                else "succeeded"
            ),
        )
    return 1 if errors else 0
