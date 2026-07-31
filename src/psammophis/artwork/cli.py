import argparse
import sys
from pathlib import Path

from psammophis.medialib.tmdb import TmdbClient
from psammophis.medialib.walk import walk_media_files

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    target = Path(args.path)
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
    tmdb = TmdbClient(cfg.tmdb_api_key, cfg.user_agent)
    errors = 0
    for video in files:
        try:
            writes = build_plan(identify(video, args.tmdb_id), tmdb, args.type)
        except ArtworkError as exc:
            errors += 1
            print(f"[ERROR] {exc}", file=sys.stderr)
            continue
        print(video)
        for write in writes:
            print(f"    {write.kind}: {write.destination}")
        if args.yes:
            for warning in execute(writes, tmdb):
                print(f"[WARNING] {warning}", file=sys.stderr)
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} errors={errors}")
    return 1 if errors else 0
