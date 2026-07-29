import argparse
import os
import sys
from pathlib import Path

from . import organize as organize_mod
from .config import ConfigError, load_config
from .opensubtitles import OpenSubtitlesClient
from .organize import VIDEO_EXTENSIONS
from .parse import parse
from .tmdb import TmdbClient

SKIP_DIR_NAMES = {"@eaDir", "#recycle"}


def _walk_inbox(inbox: Path, path_filter: str | None = None, limit: int | None = None):
    count = 0
    for dirpath, dirnames, filenames in os.walk(inbox):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            abs_path = Path(dirpath) / name
            rel = str(abs_path.relative_to(inbox))
            if path_filter and path_filter.lower() not in rel.lower():
                continue
            yield abs_path
            count += 1
            if limit and count >= limit:
                return


def _build_plan(cfg, tmdb, abs_path: Path):
    parsed = parse(abs_path)
    if parsed is None:
        return organize_mod.OrganizeResult(
            abs_path, "error", "guessit could not parse this filename"
        )
    if parsed.kind == "movie":
        return organize_mod.build_movie_plan(cfg, tmdb, abs_path)
    return organize_mod.build_episode_plan(cfg, tmdb, abs_path)


def cmd_organize(args):
    try:
        cfg = load_config(args.env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    tmdb = TmdbClient(cfg.tmdb_api_key, cfg.user_agent)
    opensubs = None
    if cfg.opensubtitles_api_key:
        opensubs = OpenSubtitlesClient(
            cfg.opensubtitles_api_key,
            cfg.user_agent,
            cfg.opensubtitles_username,
            cfg.opensubtitles_password,
        )
        opensubs.login()

    inbox = Path(args.inbox or cfg.inbox_dir)
    moved = reviewed = errors = planned = 0

    for abs_path in _walk_inbox(inbox, args.path, args.limit):
        plan_or_result = _build_plan(cfg, tmdb, abs_path)

        if isinstance(plan_or_result, organize_mod.OrganizeResult):
            result = plan_or_result
            if result.status == "review":
                reviewed += 1
                print(f"  [REVIEW] {result.source}: {result.detail}")
            else:
                errors += 1
                print(f"  [ERROR] {result.source}: {result.detail}", file=sys.stderr)
            continue

        plan = plan_or_result
        if not args.yes:
            planned += 1
            print(f"  {plan.source}")
            print(f"      -> {plan.video_path}")
            print(f"      confidence={plan.confidence:.2f} ({plan.match_reason})")
            print(f"      nfo: {plan.nfo_path}")
            if plan.poster_path:
                print(f"      poster: {plan.poster_path}")
            if plan.fanart_path:
                print(f"      fanart: {plan.fanart_path}")
            for lang, dest in plan.subtitle_plan:
                print(f"      subtitle [{lang}]: {dest}")
            continue

        result = organize_mod.execute_plan(plan, tmdb, opensubs, copy_instead_of_move=args.copy)
        if result.status == "moved":
            moved += 1
            print(f"  [OK] {result.source} -> {result.destination}  ({result.detail})")
        else:
            errors += 1
            print(f"  [ERROR] {result.source}: {result.detail}", file=sys.stderr)

    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute for real)"
    print(f"\n[{mode}] moved={moved} planned={planned} needs_review={reviewed} errors={errors}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mediaorganizer",
        description="Identify (TMDB), rename, and fetch artwork/NFO/subtitles for inbox files",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "organize",
        help="Identify and organize inbox files into Movies/TV Shows directories "
        "(dry-run unless --yes)",
    )
    sp.add_argument("--env-file", default=".env", help="Path to the .env config file")
    sp.add_argument("--inbox", help="Override MEDIAORGANIZER_INBOX_DIR for this run")
    sp.add_argument(
        "--path",
        help="Only consider files whose path (relative to the inbox) contains this substring",
    )
    sp.add_argument("--limit", type=int, help="Stop after N files")
    sp.add_argument(
        "--yes",
        action="store_true",
        help="Actually move files and fetch artwork/subtitles/write NFOs (default is a dry run)",
    )
    sp.add_argument(
        "--copy",
        action="store_true",
        help="Copy instead of move, leaving the inbox file in place (safer first run)",
    )
    sp.set_defaults(func=cmd_organize)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
