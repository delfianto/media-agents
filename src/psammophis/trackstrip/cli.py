import argparse
import shutil
import sys
import time
from pathlib import Path

from psammophis.runtime.context import AppContext
from psammophis.runtime.events import (
    ItemCompleted,
    ItemProgress,
    ItemStarted,
    PhaseCompleted,
    PhaseStarted,
    RunHeartbeat,
)
from psammophis.runtime.filesystem import RecoveryRequired
from psammophis.runtime.roots import (
    RootError,
    resolve_default_root,
    root_option_source,
    validate_deletion_target,
    validate_root,
)
from psammophis.runtime.signals import CancellationRequested

from . import apply as apply_mod
from . import langs, track_policy
from . import scan as scan_mod
from . import stats as stats_mod


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _policy_from_args(args):
    drop_codecs = frozenset(
        c.strip().lower() for c in (args.drop_audio_codec or "").split(",") if c.strip()
    )
    return track_policy.Policy(
        keep_unknown=not args.strip_unknown,
        keep_forced_subs=not args.strip_forced,
        strip_commentary=args.strip_commentary,
        detect_anime=not args.no_detect_anime,
        drop_audio_codecs=drop_codecs,
        single_audio_track=args.single_audio_track,
        drop_sdh_subs=args.drop_sdh,
    )


def add_policy_args(p):
    p.add_argument(
        "--strip-unknown",
        action="store_true",
        help="Also strip audio/subtitle tracks tagged und/unknown language (default: kept)",
    )
    p.add_argument(
        "--strip-forced",
        action="store_true",
        help="Also strip non-English forced subtitles (default: forced subs always kept)",
    )
    p.add_argument(
        "--strip-commentary",
        action="store_true",
        help="Also strip English commentary audio tracks (default: kept)",
    )
    p.add_argument(
        "--no-detect-anime",
        action="store_true",
        help="Disable anime handling (Japanese-original releases: <=2 audio tracks "
        "incl. Japanese normally keep JP audio + EN/JP subs instead of EN audio)",
    )
    p.add_argument(
        "--single-audio-track",
        action="store_true",
        help="Keep only one audio track per file (prefers non-commentary, then the "
        "default-flagged track) - drops downmixes/duplicate-master extras",
    )
    p.add_argument(
        "--drop-sdh",
        action="store_true",
        help="Drop SDH subtitle tracks when a plain (non-SDH) sibling of the same "
        "language survives; detected via disposition flag, title, or byte-size "
        "heuristic when neither is present",
    )
    p.add_argument(
        "--drop-audio-codec",
        default="",
        help="Comma-separated ffprobe codec_name(s) to drop regardless of language "
        "(e.g. 'dts'). The existing zero-audio safety net still applies: a file "
        "whose ONLY audio track matches is left untouched, not silenced.",
    )


def cmd_scan(args):
    context: AppContext | None = getattr(args, "_context", None)
    emitter = (
        context.start_run(
            command="track-strip scan",
            root=args.root,
            root_source=getattr(args, "_root_source", None),
            mode="state-write",
            wants_journal=True,
        )
        if context is not None
        else None
    )
    started = time.monotonic()
    if emitter is not None:
        emitter.emit(PhaseStarted, phase="scan")

    def progress(done, total, rel):
        if emitter is None and total:
            scan_mod._default_progress(done, total, rel)

    def item_finished(done, total, rel, error):
        if emitter is None:
            return
        emitter.emit(ItemStarted, item=rel, index=done, total=total)
        emitter.emit(
            ItemProgress,
            item=rel,
            phase="scan",
            percent=100.0,
        )
        emitter.emit(
            ItemCompleted,
            item=rel,
            status="failed" if error else "succeeded",
            detail=error,
        )

    cache = scan_mod.scan(
        args.root,
        args.cache,
        jobs=args.jobs,
        force=args.force,
        on_progress=progress,
        on_item=item_finished,
    )
    n = len(cache["files"])
    errors = sum(1 for e in cache["files"].values() if "error" in e)
    print(f"Scanned {n} files ({errors} errors). Cache: {args.cache}")
    if emitter is not None:
        emitter.emit(
            PhaseCompleted,
            phase="scan",
            status="failed" if errors else "succeeded",
            elapsed_seconds=time.monotonic() - started,
        )
    if context is not None:
        context.record_outcome(errors=errors, status="failed" if errors else "succeeded")
    return 1 if errors else 0


def cmd_stats(args):
    context: AppContext | None = getattr(args, "_context", None)
    if context is not None:
        context.start_run(
            command="track-strip stats",
            root=args.root,
            root_source=getattr(args, "_root_source", None),
            mode="read-only",
        )
    cache = scan_mod.load_cache(args.cache)
    if not cache["files"]:
        print("No cache found -- run `scan` first.", file=sys.stderr)
        return 1
    policy = _policy_from_args(args)
    report = stats_mod.build_report(cache, policy)
    stats_mod.print_report(report, policy)
    if args.show_errors and report["error_files"]:
        print("\nFiles with probe errors:")
        for rel, e in report["error_files"].items():
            print(f"  {rel}: {e['error']}")


def _print_plan_line(rel, plan_result):
    tag = "  [anime: keep JP audio, EN+JP subs]" if plan_result.get("is_anime") else ""
    print(f"  {rel}{tag}")
    for t, reason in plan_result["drop_audio"]:
        print(f"      - drop audio #{t['index']} {langs.display_name(t['language'])} [{reason}]")
    for t, reason in plan_result["drop_subtitle"]:
        print(f"      - drop sub   #{t['index']} {langs.display_name(t['language'])} [{reason}]")
    if plan_result.get("fallback_audio_used"):
        print(
            "      ! no English/unknown audio found -- "
            "kept original default track as a safety fallback"
        )


def cmd_plan(args):
    context: AppContext | None = getattr(args, "_context", None)
    if context is not None:
        context.start_run(
            command="track-strip plan",
            root=args.root,
            root_source=getattr(args, "_root_source", None),
            mode="dry-run",
        )
    cache = scan_mod.load_cache(args.cache)
    if not cache["files"]:
        print("No cache found -- run `scan` first.", file=sys.stderr)
        return 1
    policy = _policy_from_args(args)
    count = audio_drop = sub_drop = 0
    for rel, _entry, plan_result in apply_mod.candidates_from_cache(cache, policy, args.path):
        if not plan_result["changed"]:
            continue
        if args.limit and count >= args.limit:
            break
        _print_plan_line(rel, plan_result)
        count += 1
        audio_drop += len(plan_result["drop_audio"])
        sub_drop += len(plan_result["drop_subtitle"])
    print(
        f"\n{count} file(s) would change "
        f"({audio_drop} audio tracks, {sub_drop} subtitle tracks dropped)."
    )
    print(
        "Computed from the cache (fast, approximate). Run `apply` without --yes for an "
        "authoritative live per-file dry run, or `apply --yes` to execute."
    )


def cmd_apply(args):
    root = Path(args.root)
    policy = _policy_from_args(args)
    backup_dir = (
        None
        if args.no_backup
        else (args.backup_dir or str(root / ".cache" / "trackstrip" / "originals"))
    )

    exclude_dirs = frozenset({Path(backup_dir).resolve()}) if backup_dir else frozenset()

    candidates = list(apply_mod.iter_target_files(root, args.path, args.limit, exclude_dirs))
    context: AppContext | None = getattr(args, "_context", None)
    emitter = (
        context.start_run(
            command="track-strip apply",
            root=root,
            root_source=getattr(args, "_root_source", None),
            mode="applied" if args.yes else "dry-run",
            items_total=len(candidates),
            wants_journal=args.yes,
        )
        if context is not None
        else None
    )
    if args.yes and backup_dir is None:
        warning = "Originals will be permanently replaced without backups."
        if context is not None:
            context.message(warning, level="warning")
        else:
            print(f"!! {warning}", file=sys.stderr)

    changed = unchanged = errors = planned = 0
    for index, abs_path in enumerate(candidates, start=1):
        rel = str(abs_path.relative_to(root))
        if emitter is not None:
            emitter.emit(ItemStarted, item=rel, index=index, total=len(candidates))
        phase_times: dict[str, float] = {}

        def on_phase(
            phase: str,
            state: str,
            _phase_times: dict[str, float] = phase_times,
            _rel: str = rel,
        ) -> None:
            if emitter is None:
                return
            if state == "started":
                _phase_times[phase] = time.monotonic()
                emitter.emit(PhaseStarted, phase=phase, item=_rel)
            else:
                started = _phase_times.pop(phase, None)
                emitter.emit(
                    PhaseCompleted,
                    phase=phase,
                    item=_rel,
                    status=state if state in ("failed", "cancelled") else "succeeded",
                    elapsed_seconds=time.monotonic() - started if started is not None else None,
                )

        def on_heartbeat(phase: str, _rel: str = rel) -> None:
            if emitter is not None:
                emitter.emit(RunHeartbeat, phase=phase, item=_rel, message="still running")

        try:
            result, plan_result = apply_mod.apply_one(
                abs_path,
                root,
                policy,
                backup_dir,
                execute=args.yes,
                on_phase=on_phase,
                on_heartbeat=on_heartbeat if args.yes else None,
            )
        except KeyboardInterrupt, CancellationRequested:
            if emitter is not None:
                emitter.emit(ItemCompleted, item=rel, status="cancelled")
            raise
        except RecoveryRequired as exc:
            if emitter is not None:
                emitter.emit(ItemCompleted, item=rel, status="failed", detail=str(exc))
            raise
        if result.status == "unchanged":
            unchanged += 1
        elif result.status == "planned":
            planned += 1
            _print_plan_line(result.rel, plan_result)
            print(f"      $ {result.detail}")
        elif result.status == "changed":
            changed += 1
            print(f"  [OK] {result.rel}  ({result.detail})")
        elif result.status == "error":
            errors += 1
            if context is not None:
                context.message(result.detail, level="error", item=rel)
            else:
                print(f"  [ERROR] {result.rel}: {result.detail}", file=sys.stderr)
        if emitter is not None:
            item_status = {
                "changed": "succeeded",
                "planned": "skipped",
                "unchanged": "skipped",
                "error": "failed",
            }[result.status]
            emitter.emit(
                ItemCompleted,
                item=rel,
                status=item_status,
                detail=result.detail or None,
            )

    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute for real)"
    print(f"\n[{mode}] changed={changed} planned={planned} unchanged={unchanged} errors={errors}")
    if args.yes and backup_dir and changed:
        print(f"Backups of changed files were retained under: {backup_dir}")
    if context is not None:
        context.record_outcome(
            status="succeeded"
            if not errors
            else ("partial" if changed or planned or unchanged else "failed"),
            changed=changed,
            planned=planned,
            errors=errors,
        )
    return 1 if errors else 0


def cmd_transcode(args):
    root = Path(args.root)
    from_codecs = frozenset(c.strip().lower() for c in args.from_codec.split(",") if c.strip())
    backup_dir = (
        None
        if args.no_backup
        else (args.backup_dir or str(root / ".cache" / "trackstrip" / "originals"))
    )

    exclude_dirs = frozenset({Path(backup_dir).resolve()}) if backup_dir else frozenset()

    candidates = list(apply_mod.iter_target_files(root, args.path, args.limit, exclude_dirs))
    context: AppContext | None = getattr(args, "_context", None)
    emitter = (
        context.start_run(
            command="track-strip transcode",
            root=root,
            root_source=getattr(args, "_root_source", None),
            mode="applied" if args.yes else "dry-run",
            items_total=len(candidates),
            wants_journal=args.yes,
        )
        if context is not None
        else None
    )
    if args.yes and backup_dir is None:
        warning = "Originals will be permanently replaced without backups."
        if context is not None:
            context.message(warning, level="warning")
        else:
            print(f"!! {warning}", file=sys.stderr)

    changed = unchanged = errors = planned = 0
    for index, abs_path in enumerate(candidates, start=1):
        rel = str(abs_path.relative_to(root))
        if emitter is not None:
            emitter.emit(ItemStarted, item=rel, index=index, total=len(candidates))
        phase_times: dict[str, float] = {}

        def on_phase(
            phase: str,
            state: str,
            _phase_times: dict[str, float] = phase_times,
            _rel: str = rel,
        ) -> None:
            if emitter is None:
                return
            if state == "started":
                _phase_times[phase] = time.monotonic()
                emitter.emit(PhaseStarted, phase=phase, item=_rel)
            else:
                started = _phase_times.pop(phase, None)
                emitter.emit(
                    PhaseCompleted,
                    phase=phase,
                    item=_rel,
                    status=state if state in ("failed", "cancelled") else "succeeded",
                    elapsed_seconds=time.monotonic() - started if started is not None else None,
                )

        def on_heartbeat(phase: str, _rel: str = rel) -> None:
            if emitter is not None:
                emitter.emit(RunHeartbeat, phase=phase, item=_rel, message="still running")

        try:
            result, plan_result = apply_mod.transcode_one(
                abs_path,
                root,
                from_codecs,
                args.to_codec,
                args.bitrate,
                backup_dir,
                execute=args.yes,
                on_phase=on_phase,
                on_heartbeat=on_heartbeat if args.yes else None,
            )
        except KeyboardInterrupt, CancellationRequested:
            if emitter is not None:
                emitter.emit(ItemCompleted, item=rel, status="cancelled")
            raise
        except RecoveryRequired as exc:
            if emitter is not None:
                emitter.emit(ItemCompleted, item=rel, status="failed", detail=str(exc))
            raise
        if result.status == "unchanged":
            unchanged += 1
        elif result.status == "planned":
            planned += 1
            assert plan_result is not None  # only None on the "error" status path
            print(f"  {result.rel}")
            for s in plan_result["matching"]:
                print(
                    f"      - transcode audio #{s['index']} {s.get('codec_name')} -> "
                    f"{args.to_codec}@{args.bitrate}"
                )
            print(f"      $ {result.detail}")
        elif result.status == "changed":
            changed += 1
            print(f"  [OK] {result.rel}  ({result.detail})")
        elif result.status == "error":
            errors += 1
            if context is not None:
                context.message(result.detail, level="error", item=rel)
            else:
                print(f"  [ERROR] {result.rel}: {result.detail}", file=sys.stderr)
        if emitter is not None:
            item_status = {
                "changed": "succeeded",
                "planned": "skipped",
                "unchanged": "skipped",
                "error": "failed",
            }[result.status]
            emitter.emit(
                ItemCompleted,
                item=rel,
                status=item_status,
                detail=result.detail or None,
            )

    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute for real)"
    print(f"\n[{mode}] changed={changed} planned={planned} unchanged={unchanged} errors={errors}")
    if args.yes and backup_dir and changed:
        print(f"Backups of changed files were retained under: {backup_dir}")
    if context is not None:
        context.record_outcome(
            status="succeeded"
            if not errors
            else ("partial" if changed or planned or unchanged else "failed"),
            changed=changed,
            planned=planned,
            errors=errors,
        )
    return 1 if errors else 0


def cmd_purge_backups(args):
    root = Path(args.root)
    context: AppContext | None = getattr(args, "_context", None)
    try:
        backup_dir = validate_deletion_target(args.backup_dir, media_root=root)
    except RootError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        if context is not None:
            context.record_outcome(status="failed", errors=1)
        return 2
    if context is not None:
        context.start_run(
            command="track-strip purge-backups",
            root=root,
            root_source=getattr(args, "_root_source", None),
            mode="applied" if args.yes else "dry-run",
            items_total=1,
            wants_journal=args.yes,
        )
    if not backup_dir.exists():
        print(f"No backup directory at {backup_dir}, nothing to purge.")
        return 0
    size = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file())
    if not args.yes:
        print(
            f"Would permanently delete {backup_dir} ({stats_mod.human_size(size)}). "
            "Re-run with --yes to confirm."
        )
        return 0
    shutil.rmtree(backup_dir)
    print(f"Deleted {backup_dir} ({stats_mod.human_size(size)} freed).")
    if context is not None:
        context.record_outcome(changed=1)
    return 0


def build_parser(default_root, default_cache):
    p = argparse.ArgumentParser(
        prog="psammophis track-strip",
        description="Plex media library audio/subtitle track-trimming toolkit",
    )
    p.add_argument("--root", default=default_root, help="Media library root")
    p.add_argument("--cache", default=default_cache, help="Path to scan cache JSON")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="Probe all media files with ffprobe and update the cache")
    sp.add_argument(
        "--jobs",
        type=_positive_int,
        default=8,
        help="Parallel ffprobe workers (default 8)",
    )
    sp.add_argument(
        "--force", action="store_true", help="Re-probe every file, ignoring cache freshness"
    )
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("stats", help="Print codec/language statistics from the cache")
    sp.add_argument("--show-errors", action="store_true")
    add_policy_args(sp)
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("plan", help="Fast cache-based preview of what `apply` would strip")
    sp.add_argument(
        "--path", help="Only consider files whose relative path contains this substring"
    )
    sp.add_argument("--limit", type=int, help="Stop after N changed files")
    add_policy_args(sp)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser(
        "apply", help="Remux files to strip non-English audio/subtitles (dry-run unless --yes)"
    )
    sp.add_argument(
        "--path", help="Only consider files whose relative path contains this substring"
    )
    sp.add_argument("--limit", type=int, help="Stop after N files")
    sp.add_argument(
        "--yes", action="store_true", help="Actually execute the remux (default is a live dry run)"
    )
    sp.add_argument(
        "--no-backup",
        action="store_true",
        help="Replace originals without retaining backups",
    )
    sp.add_argument(
        "--backup-dir",
        help="Where to move stripped originals (default: <root>/.cache/trackstrip/originals)",
    )
    add_policy_args(sp)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser(
        "transcode",
        help="Re-encode audio tracks of a given codec to a more compatible "
        "one (dry-run unless --yes); video is always stream-copied",
    )
    sp.add_argument(
        "--from-codec",
        default="dts",
        help="Comma-separated ffprobe codec_name(s) to transcode away from (default: dts)",
    )
    sp.add_argument("--to-codec", default="eac3", help="Target audio codec (default: eac3)")
    sp.add_argument("--bitrate", default="640k", help="Target audio bitrate (default: 640k)")
    sp.add_argument(
        "--path", help="Only consider files whose relative path contains this substring"
    )
    sp.add_argument("--limit", type=int, help="Stop after N files")
    sp.add_argument(
        "--yes",
        action="store_true",
        help="Actually execute the transcode (default is a live dry run)",
    )
    sp.add_argument(
        "--no-backup",
        action="store_true",
        help="Replace originals without retaining backups",
    )
    sp.add_argument(
        "--backup-dir",
        help="Where to retain originals as backups (default: <root>/.cache/trackstrip/originals)",
    )
    sp.set_defaults(func=cmd_transcode)

    sp = sub.add_parser(
        "purge-backups", help="Permanently delete the backup directory of stripped originals"
    )
    sp.add_argument("--backup-dir", default=None)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_purge_backups)

    return p


def main(argv=None, context: AppContext | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    default = resolve_default_root(feature_env="TRACKSTRIP_ROOT")
    default_root = str(default.path)
    default_cache = str(default.path / ".cache" / "trackstrip" / "scan.json")
    parser = build_parser(default_root, default_cache)
    args = parser.parse_args(raw)
    if args.command in ("scan", "apply", "transcode", "purge-backups"):
        try:
            root = validate_root(args.root)
        except RootError as exc:
            print(f"Invalid media root: {exc}", file=sys.stderr)
            return 2
        args.root = str(root)
    args._root_source = root_option_source(raw, default)
    if args.command == "purge-backups" and args.backup_dir is None:
        args.backup_dir = str(Path(args.root) / ".cache" / "trackstrip" / "originals")
    args._context = context
    result = args.func(args)
    return 0 if result is None else int(result)


if __name__ == "__main__":
    main()
