import argparse
import sys
from pathlib import Path

from medialib.walk import walk_media_files

from .config import ConfigError, load_config
from .core import VIDEO_EXTENSIONS, SubtitleError, build_plans, execute
from .opensubtitles import OpenSubtitlesClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subtitle", description="Fetch external subtitles")
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    target = Path(args.path)
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
    languages = tuple(args.language) if args.language else cfg.languages
    client = None
    if args.yes:
        if not cfg.api_key:
            raise SystemExit("SUBTITLE_OPENSUBTITLES_API_KEY is required to fetch subtitles")
        client = OpenSubtitlesClient(cfg.api_key, cfg.user_agent, cfg.username, cfg.password)
        client.login()
    errors = 0
    for video in files:
        try:
            plans = build_plans(video, languages)
            for plan in plans:
                if args.yes:
                    assert client is not None
                    result = execute(plan, client, overwrite=args.overwrite)
                    print(f"[{result.upper()}] {plan.destination}")
                else:
                    print(f"{video}\n    subtitle [{plan.language}]: {plan.destination}")
        except SubtitleError as exc:
            errors += 1
            print(f"[ERROR] {exc}", file=sys.stderr)
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} errors={errors}")
