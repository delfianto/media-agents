import argparse
import shlex
import sys
from pathlib import Path

from psammophis.medialib.walk import walk_media_files

from .command import FLAG_NAMES, Edits
from .runner import apply


def _flag(value: str) -> tuple[str, bool]:
    name, separator, raw = value.partition("=")
    if not separator or name not in FLAG_NAMES or raw not in ("yes", "no"):
        raise argparse.ArgumentTypeError("use NAME=yes|no with a supported flag name")
    return name, raw == "yes"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psammophis mkvedit", description="Safely edit Matroska metadata without remuxing"
    )
    parser.add_argument("--path", required=True, help="An MKV file or directory")
    parser.add_argument("--filter", help="Only relative paths containing this substring")
    parser.add_argument("--limit", type=int)
    title = parser.add_mutually_exclusive_group()
    title.add_argument("--title")
    title.add_argument("--delete-title", action="store_true")
    parser.add_argument("--track")
    name = parser.add_mutually_exclusive_group()
    name.add_argument("--track-name")
    name.add_argument("--delete-track-name", action="store_true")
    parser.add_argument("--language")
    parser.add_argument("--flag", action="append", type=_flag, default=[])
    parser.add_argument("--default-track")
    parser.add_argument("--default-audio")
    parser.add_argument("--default-subtitle")
    parser.add_argument("--default-video")
    cover = parser.add_mutually_exclusive_group()
    cover.add_argument("--cover", type=Path)
    cover.add_argument("--delete-cover", action="store_true")
    parser.add_argument("--attachment-name")
    parser.add_argument("--attachment-mime-type")
    tags = parser.add_mutually_exclusive_group()
    tags.add_argument("--tags", type=Path)
    tags.add_argument("--delete-tags", action="store_true")
    chapters = parser.add_mutually_exclusive_group()
    chapters.add_argument("--chapters", type=Path)
    chapters.add_argument("--delete-chapters", action="store_true")
    parser.add_argument("--backup-suffix", default=".mkvedit.bak")
    parser.add_argument("--yes", action="store_true")
    return parser


def _edits(args: argparse.Namespace) -> Edits:
    defaults: dict[str, str] = {}
    if args.default_audio:
        defaults["audio"] = args.default_audio
    if args.default_subtitle:
        defaults["subtitle"] = args.default_subtitle
    if args.default_video:
        defaults["video"] = args.default_video
    if args.default_track:
        defaults["*"] = args.default_track
    return Edits(
        title=args.title,
        delete_title=args.delete_title,
        track_selector=args.track,
        track_name=args.track_name,
        delete_track_name=args.delete_track_name,
        language=args.language,
        flags=dict(args.flag),
        defaults=defaults,
        cover=args.cover,
        delete_cover=args.delete_cover,
        attachment_name=args.attachment_name,
        attachment_mime_type=args.attachment_mime_type,
        tags=args.tags,
        delete_tags=args.delete_tags,
        chapters=args.chapters,
        delete_chapters=args.delete_chapters,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        print(f"path does not exist: {target}", file=sys.stderr)
        return 2
    if target.is_file() and target.suffix.lower() != ".mkv":
        print(f"mkvedit only supports .mkv files: {target}", file=sys.stderr)
        return 2
    files = list(
        [target]
        if target.is_file()
        else walk_media_files(
            target,
            frozenset({".mkv"}),
            path_filter=args.filter,
            limit=args.limit,
            skip_root_files=False,
        )
    )
    edits = _edits(args)
    errors = 0
    for path in files:
        result = apply(
            path,
            edits,
            yes=args.yes,
            backup_suffix=args.backup_suffix,
        )
        if result.command:
            print(f"{path}\n    {shlex.join(result.command)}")
        if result.status == "error":
            errors += 1
            print(f"[ERROR] {result.detail}", file=sys.stderr)
        elif args.yes:
            print(f"[EDITED] backup: {result.backup}")
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} errors={errors}")
    return 1 if errors else 0
