import argparse
import sys
from pathlib import Path

from medialib.tmdb import TmdbClient
from medialib.walk import walk_media_files

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
        prog="organize", description="Identify and organize inbox media files"
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    tmdb = TmdbClient(cfg.tmdb_api_key, cfg.user_agent)
    inbox = Path(args.inbox or cfg.inbox_dir)
    counts = {"moved": 0, "planned": 0, "review": 0, "error": 0}
    files = walk_media_files(
        inbox,
        VIDEO_EXTENSIONS,
        path_filter=args.path,
        limit=args.limit,
        skip_root_files=False,
    )
    for source in files:
        parsed = parse(source)
        result: Plan | OrganizeResult
        if parsed is None:
            result = OrganizeResult(source, "error", "guessit could not parse filename")
        elif parsed.kind == "movie":
            result = build_movie_plan(cfg, tmdb, source)
        else:
            result = build_episode_plan(cfg, tmdb, source)
        if isinstance(result, OrganizeResult):
            counts[result.status] += 1
            stream = sys.stderr if result.status == "error" else sys.stdout
            print(f"[{result.status.upper()}] {result.source}: {result.detail}", file=stream)
            continue
        if not args.yes:
            counts["planned"] += 1
            print(f"{result.source}\n    -> {result.video_path}")
            print(f"    confidence={result.confidence:.2f} ({result.match_reason})")
            continue
        applied = execute_plan(result, copy_instead_of_move=args.copy, overwrite=args.overwrite)
        counts[applied.status] += 1
        print(f"[{applied.status.upper()}] {applied.source}: {applied.detail}")
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] " + " ".join(f"{key}={value}" for key, value in counts.items()))
