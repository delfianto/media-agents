import argparse
import shlex
import sys
import time
from pathlib import Path

from psammophis.medialib.walk import walk_media_files
from psammophis.runtime.context import AppContext
from psammophis.runtime.events import (
    ItemCompleted,
    ItemStarted,
    PhaseCompleted,
    PhaseStarted,
    RunHeartbeat,
)
from psammophis.runtime.signals import CancellationRequested

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


def main(argv: list[str] | None = None, context: AppContext | None = None) -> int:
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
    emitter = (
        context.start_run(
            command="mkvedit",
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
    edits = _edits(args)
    errors = 0
    for index, path in enumerate(files, start=1):
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(path), index=index, total=len(files))
        phase_times: dict[str, float] = {}

        def on_phase(
            phase: str,
            state: str,
            _phase_times: dict[str, float] = phase_times,
            _path: Path = path,
        ) -> None:
            if emitter is None:
                return
            if state == "started":
                _phase_times[phase] = time.monotonic()
                emitter.emit(PhaseStarted, phase=phase, item=str(_path))
                return
            started = _phase_times.pop(phase, None)
            emitter.emit(
                PhaseCompleted,
                phase=phase,
                item=str(_path),
                status=state if state in ("failed", "cancelled") else "succeeded",
                elapsed_seconds=time.monotonic() - started if started is not None else None,
            )

        def on_heartbeat(phase: str, _path: Path = path) -> None:
            if emitter is not None:
                emitter.emit(RunHeartbeat, phase=phase, item=str(_path), message="still running")

        try:
            result = apply(
                path,
                edits,
                yes=args.yes,
                backup_suffix=args.backup_suffix,
                on_phase=on_phase,
                on_heartbeat=on_heartbeat if args.yes else None,
            )
        except KeyboardInterrupt, CancellationRequested:
            if emitter is not None:
                emitter.emit(ItemCompleted, item=str(path), status="cancelled")
            raise
        if result.command:
            print(f"{path}\n    {shlex.join(result.command)}")
        if result.status == "error":
            errors += 1
            if context is not None:
                context.message(result.detail, level="error", item=str(path), phase="edit")
            else:
                print(f"[ERROR] {result.detail}", file=sys.stderr)
        elif args.yes:
            print(f"[EDITED] backup: {result.backup}")
        if emitter is not None:
            failed = result.status == "error"
            emitter.emit(
                ItemCompleted,
                item=str(path),
                status="failed" if failed else ("succeeded" if args.yes else "skipped"),
                detail=result.detail,
            )
    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute)"
    print(f"[{mode}] files={len(files)} errors={errors}")
    if context is not None:
        context.record_outcome(
            changed=len(files) - errors if args.yes else None,
            errors=errors,
            status="partial"
            if errors and len(files) > errors
            else ("failed" if errors else "succeeded"),
        )
    return 1 if errors else 0
