import argparse
import sys
from pathlib import Path

from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import ItemCompleted, ItemStarted, PhaseCompleted, PhaseStarted

from .config import ConfigError, load_config
from .core import VIDEO_EXTENSIONS, SubtitleError, build_plans, execute
from .opensubtitles import OpenSubtitlesClient, OpenSubtitlesError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis subtitle", description="Fetch external subtitles"
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--path", required=True, help="An organized video file or directory")
    parser.add_argument(
        "--language",
        action="append",
        help="OpenSubtitles language code; repeatable (defaults to SUBTITLE_LANGUAGES)",
    )
    parser.add_argument("--filter", help="Only relative paths containing this substring")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
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
            command="subtitle",
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
    languages = tuple(args.language) if args.language else cfg.languages
    client = None
    if args.yes:
        if not cfg.api_key:
            text = "SUBTITLE_OPENSUBTITLES_API_KEY is required to fetch subtitles"
            print(text, file=sys.stderr)
            if context is not None:
                context.record_outcome(status="failed", errors=1)
            return 2
        client = OpenSubtitlesClient(cfg.api_key, cfg.user_agent, cfg.username, cfg.password)
        try:
            client.login()
        except OpenSubtitlesError as exc:
            if context is not None:
                context.message(str(exc), level="error", phase="login")
                context.record_outcome(status="failed", errors=1)
            else:
                print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
    errors = changed = planned = 0
    for index, video in enumerate(files, start=1):
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(video), index=index, total=len(files))
            emitter.emit(PhaseStarted, phase="plan", item=str(video))
        active_phase = "plan"
        item_changed = 0
        try:
            plans = build_plans(video, languages)
            if emitter is not None:
                emitter.emit(PhaseCompleted, phase="plan", item=str(video), status="succeeded")
            for plan in plans:
                if args.yes:
                    assert client is not None
                    if emitter is not None:
                        emitter.emit(PhaseStarted, phase="download", item=str(video))
                    active_phase = "download"
                    result = execute(plan, client, overwrite=args.overwrite)
                    print(f"[{result.upper()}] {plan.destination}")
                    if result == "downloaded":
                        changed += 1
                        item_changed += 1
                    if emitter is not None:
                        emitter.emit(
                            PhaseCompleted,
                            phase="download",
                            item=str(video),
                            status="succeeded",
                        )
                    active_phase = "plan"
                else:
                    print(f"{video}\n    subtitle [{plan.language}]: {plan.destination}")
                    planned += 1
        except SubtitleError as exc:
            errors += 1
            if context is not None:
                context.message(str(exc), level="error", item=str(video))
            else:
                print(f"[ERROR] {exc}", file=sys.stderr)
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase=active_phase,
                    item=str(video),
                    status="failed",
                )
                emitter.emit(ItemCompleted, item=str(video), status="failed", detail=str(exc))
            continue
        if emitter is not None:
            emitter.emit(
                ItemCompleted,
                item=str(video),
                status="succeeded" if item_changed else "skipped",
                detail=None if item_changed else "no subtitle downloaded",
            )
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} changed={changed} planned={planned} errors={errors}")
    if context is not None:
        context.record_outcome(
            changed=changed,
            planned=planned,
            errors=errors,
            status="partial"
            if errors and (changed or planned)
            else ("failed" if errors else "succeeded"),
        )
    return 1 if errors else 0
