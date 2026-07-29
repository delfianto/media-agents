import argparse
import os
import shutil
import sys
from pathlib import Path

from . import colorinfo, presets
from . import gpu as gpu_mod
from . import run as run_mod
from .probe import probe_file

DEFAULT_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".ts", ".mov"}
SKIP_DIR_NAMES = {"@eaDir", "#recycle"}


def _human_size(n: int | None) -> str:
    if not n:
        return "0 B"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _walk_media_files(root: Path, path_filter: str | None = None, limit: int | None = None):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")
        )
        if Path(dirpath) == root:
            continue  # loose files at the library root aren't organized into Movies/TV Shows yet
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in DEFAULT_EXTENSIONS:
                continue
            abs_path = Path(dirpath) / name
            rel = str(abs_path.relative_to(root))
            if path_filter and path_filter.lower() not in rel.lower():
                continue
            yield abs_path
            count += 1
            if limit and count >= limit:
                return


def cmd_probe(args):
    root = Path(args.root)
    for abs_path in _walk_media_files(root, args.path, args.limit):
        rel = abs_path.relative_to(root)
        try:
            probed = probe_file(abs_path)
        except Exception as exc:
            print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
            continue
        video = probed.get("video")
        if video is None:
            print(f"  [ERROR] {rel}: no video stream found", file=sys.stderr)
            continue

        hdr = colorinfo.is_hdr(video)
        dv = colorinfo.has_dolby_vision(video)
        dynamic_range = "Dolby Vision" if dv else ("HDR10" if hdr else "SDR")
        tier = presets.resolution_tier(video["height"])
        preset = presets.select_preset(video["height"], args.profile, hdr)
        audio_desc = (
            ", ".join(
                f"{a['codec_name']}/{a['channels']}ch -> "
                f"opus@{presets.opus_bitrate_kbps(a['channels'])}k"
                for a in probed["audio"]
            )
            or "(none)"
        )
        size_desc = _human_size(probed["format"].get("size"))

        print(f"  {rel}")
        print(
            f"      {video['width']}x{video['height']} ({tier}) {video['codec_name']} "
            f"{video.get('profile') or ''} {dynamic_range}  size={size_desc}"
        )
        print(f"      preset: {preset.name} -- {preset.description}")
        print(f"      audio:  {audio_desc}")
        if dv:
            print("      note: Dolby Vision present -- `run` forces backend=cpu regardless")
            print("            of --backend (av1_nvenc cannot preserve DV RPU metadata)")


def cmd_list_presets(args):
    del args
    for (tier, profile), preset in sorted(presets.PRESETS.items()):
        print(f"{preset.name}  [{tier} / {profile}]")
        print(f"    {preset.description}")
        print(
            f"    cpu:   preset={preset.svt_preset} crf={preset.crf} tune={preset.svt_tune} "
            f"film-grain={preset.film_grain} extra={preset.svt_extra}"
        )
        print(
            f"    nvenc: preset={preset.nvenc_preset} tune={preset.nvenc_tune} "
            f"cq={preset.nvenc_cq} extra={preset.nvenc_extra}"
        )
    print(
        f"\nHDR sources (PQ/HLG transfer) get crf/cq lowered by "
        f"{presets.HDR_QUALITY_BONUS} automatically -- more bits for the same preset, "
        "since gradient banding is far more visible in HDR."
    )


def cmd_run(args):
    root = Path(args.root)
    backup_dir = (
        None
        if args.no_backup
        else (args.backup_dir or str(root / ".cache" / "av1transcode" / "originals"))
    )
    log_dir = Path(args.log_dir or str(root / ".cache" / "av1transcode" / "logs"))

    gpu_index = None
    if args.backend in ("auto", "nvenc"):
        gpu_index = gpu_mod.detect_av1_nvenc_gpu()
        if args.backend == "nvenc" and gpu_index is None:
            print(
                "No AV1-capable NVIDIA GPU detected; cannot honor --backend nvenc.", file=sys.stderr
            )
            sys.exit(1)

    if args.yes and backup_dir is None:
        print(
            "!! Running with --yes --no-backup: "
            "originals will be permanently deleted, not backed up."
        )

    changed = planned = errors = 0
    for abs_path in _walk_media_files(root, args.path, args.limit):
        rel = abs_path.relative_to(root)

        def _print_progress(line: str, rel: Path = rel) -> None:
            print(f"      {rel}: {line}")

        result, _probed = run_mod.transcode_one(
            abs_path,
            root,
            args.profile,
            args.backend,
            gpu_index,
            backup_dir,
            execute=args.yes,
            log_dir=log_dir,
            drop_subtitles=args.no_subtitles,
            on_progress=_print_progress if args.yes else None,
        )
        if result.status == "planned":
            planned += 1
            print(f"  {result.rel}")
            print(f"      $ {result.detail}")
        elif result.status == "changed":
            changed += 1
            print(f"  [OK] {result.rel}  ({result.detail})")
        elif result.status == "error":
            errors += 1
            print(f"  [ERROR] {result.rel}: {result.detail}", file=sys.stderr)

    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute for real)"
    print(f"\n[{mode}] changed={changed} planned={planned} errors={errors}")
    if args.yes and backup_dir and changed:
        print(f"Originals of changed files were moved under: {backup_dir}")
    if args.yes:
        print(f"Per-file live logs under: {log_dir}  (tail -f <file> to watch progress in full)")


def cmd_purge_backups(args):
    backup_dir = Path(args.backup_dir)
    if not backup_dir.exists():
        print(f"No backup directory at {backup_dir}, nothing to purge.")
        return
    size = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file())
    if not args.yes:
        print(f"Would permanently delete {backup_dir} ({_human_size(size)}).")
        print("Re-run with --yes to confirm.")
        return
    shutil.rmtree(backup_dir)
    print(f"Deleted {backup_dir} ({_human_size(size)} freed).")


def build_parser(default_root: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="av1transcode", description="AV1 (libsvtav1/av1_nvenc) + Opus transcode toolkit"
    )
    p.add_argument("--root", default=default_root, help="Media library root")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "probe",
        help="Read-only: report resolution/HDR/DV/audio and which preset+backend `run` would pick",
    )
    sp.add_argument(
        "--path", help="Only consider files whose relative path contains this substring"
    )
    sp.add_argument("--limit", type=int, help="Stop after N files")
    sp.add_argument(
        "--profile",
        choices=presets.PROFILES,
        default=presets.DEFAULT_PROFILE,
        help="Content profile for preset selection (default: film)",
    )
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("list-presets", help="Print the built-in resolution x profile preset table")
    sp.set_defaults(func=cmd_list_presets)

    sp = sub.add_parser(
        "run", help="Re-encode video to AV1 and audio to Opus (dry-run unless --yes)"
    )
    sp.add_argument(
        "--path", help="Only consider files whose relative path contains this substring"
    )
    sp.add_argument("--limit", type=int, help="Stop after N files")
    sp.add_argument(
        "--profile",
        choices=presets.PROFILES,
        default=presets.DEFAULT_PROFILE,
        help="Content profile for preset selection (default: film) -- pick 'anime' explicitly "
        "for animation/cartoon sources, it is never auto-detected from audio language",
    )
    sp.add_argument(
        "--backend",
        choices=("auto", "cpu", "nvenc"),
        default="auto",
        help="Encoder backend (default: auto -- nvenc if an AV1-capable GPU is found and the "
        "source has no Dolby Vision, else cpu/libsvtav1)",
    )
    sp.add_argument(
        "--yes",
        action="store_true",
        help="Actually execute (default is a dry run that prints the ffmpeg command it would run)",
    )
    sp.add_argument(
        "--no-backup", action="store_true", help="Delete originals instead of backing them up"
    )
    sp.add_argument(
        "--backup-dir",
        help="Where to move originals (default: <root>/.cache/av1transcode/originals)",
    )
    sp.add_argument(
        "--log-dir",
        help="Where to write per-file ffmpeg logs (default: <root>/.cache/av1transcode/logs)",
    )
    sp.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Drop subtitle/attachment streams instead of copying them (use if the source "
        "container's subtitle codec can't mux into Matroska)",
    )
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser(
        "purge-backups", help="Permanently delete the backup directory of originals"
    )
    sp.add_argument("--backup-dir", default=None)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_purge_backups)

    return p


def _find_library_root(start: Path) -> Path:
    """Same convention as media-library/scripts/mediatools/cli.py: the
    library root is wherever the `.agents` repo containing this script is
    checked out into."""
    for ancestor in start.parents:
        if ancestor.name == ".agents":
            return ancestor.parent
    return start.parent.parent.parent.parent.parent.parent


def main(argv=None):
    default_root = (
        os.environ.get("AV1TRANSCODE_ROOT")
        or os.environ.get("MEDIATOOLS_ROOT")
        or str(_find_library_root(Path(__file__).resolve()))
    )
    parser = build_parser(default_root)
    args = parser.parse_args(argv)
    if args.command == "purge-backups" and args.backup_dir is None:
        args.backup_dir = str(Path(args.root) / ".cache" / "av1transcode" / "originals")
    args.func(args)


if __name__ == "__main__":
    main()
