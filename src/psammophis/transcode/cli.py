import argparse
import math
import shutil
import sys
import time
from pathlib import Path

from psammophis.medialib import av1_backend, colorinfo
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.gpu import detect_av1_nvenc_gpu
from psammophis.medialib.grain import GRAIN_CPU_THRESHOLD, measure_grain
from psammophis.medialib.humansize import human_size
from psammophis.medialib.svt import detect_svt_implementation
from psammophis.medialib.videoprobe import probe_file
from psammophis.medialib.walk import walk_media_files
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

from . import config, langfilter
from . import run as run_mod

DEFAULT_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".ts", ".mov"})


def _cache_path(root: Path, leaf: str, explicit: str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return root / ".cache" / "transcode" / leaf


def _resolve(cli_value, config_value):
    """CLI flag wins if explicitly passed (argparse default is None for
    these), else fall back to the resolved .env/environment config value."""
    return cli_value if cli_value is not None else config_value


def _bitrate_fraction(value: str) -> float:
    try:
        fraction = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0 < fraction <= 1:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 1")
    return fraction


def _grain_threshold(value: str) -> float:
    try:
        threshold = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise argparse.ArgumentTypeError("must be a finite number from 0 through 1")
    return threshold


def _output_collisions(output_dir: Path, candidates: list[Path]) -> dict[Path, list[Path]]:
    destinations: dict[Path, list[Path]] = {}
    for candidate in candidates:
        destination = (output_dir / candidate.name).with_suffix(".mkv")
        destinations.setdefault(destination, []).append(candidate)
    return {path: sources for path, sources in destinations.items() if len(sources) > 1}


def cmd_probe(args):
    root = Path(args.root)
    cfg = config.load_config(args.env_file)
    audio_lang = _resolve(args.audio_lang, cfg.audio_lang)
    subtitle_lang = _resolve(args.subtitle_lang, cfg.subtitle_lang)
    single_audio_track = not args.all_audio_tracks
    svt_implementation = detect_svt_implementation()
    candidates = list(
        walk_media_files(root, DEFAULT_EXTENSIONS, path_filter=args.path, limit=args.limit)
    )
    context: AppContext | None = getattr(args, "_context", None)
    emitter = (
        context.start_run(
            command="transcode probe",
            root=root,
            root_source=getattr(args, "_root_source", None),
            mode="read-only",
            items_total=len(candidates),
        )
        if context is not None
        else None
    )
    errors = 0
    nvencc_ok = av1_backend.nvencc_available()
    gpu_index = detect_av1_nvenc_gpu()

    for index, abs_path in enumerate(candidates, start=1):
        rel = abs_path.relative_to(root)
        if emitter is not None:
            emitter.emit(ItemStarted, item=str(rel), index=index, total=len(candidates))
            emitter.emit(PhaseStarted, phase="probe", item=str(rel))
        try:
            probed = probe_file(abs_path)
        except Exception as exc:
            errors += 1
            if emitter is not None:
                emitter.emit(PhaseCompleted, phase="probe", item=str(rel), status="failed")
                if context is not None:
                    context.message(str(exc), level="error", item=str(rel), phase="probe")
                emitter.emit(ItemCompleted, item=str(rel), status="failed", detail=str(exc))
            else:
                print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
            continue
        if emitter is not None:
            emitter.emit(PhaseCompleted, phase="probe", item=str(rel), status="succeeded")
        video = probed.get("video")
        if video is None:
            errors += 1
            if emitter is not None:
                if context is not None:
                    context.message("no video stream found", level="error", item=str(rel))
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="failed",
                    detail="no video stream found",
                )
            else:
                print(f"  [ERROR] {rel}: no video stream found", file=sys.stderr)
            continue

        hdr = colorinfo.is_hdr(video)
        dv = colorinfo.has_dolby_vision(video)
        hdr10_plus = colorinfo.has_hdr10_plus(video)
        if dv:
            dynamic_range = "Dolby Vision"
        elif hdr10_plus:
            dynamic_range = "HDR10+"
        elif hdr:
            dynamic_range = "HDR10"
        else:
            dynamic_range = "SDR"
        tier = presets.resolution_tier(video["height"])
        preset = presets.select_preset(video["height"], args.profile, hdr)
        size_desc = human_size(probed["format"].get("size"))
        grain = None
        if not args.no_grain_routing and av1_backend.grain_routing_applies(
            "auto", video, gpu_index, nvencc_ok
        ):
            if emitter is not None:
                emitter.emit(PhaseStarted, phase="measure-grain", item=str(rel))
            try:
                grain = measure_grain(abs_path, probed["format"].get("duration"))
            except Exception as exc:
                errors += 1
                if emitter is not None:
                    emitter.emit(
                        PhaseCompleted,
                        phase="measure-grain",
                        item=str(rel),
                        status="failed",
                    )
                    if context is not None:
                        context.message(
                            str(exc),
                            level="error",
                            item=str(rel),
                            phase="measure-grain",
                        )
                    emitter.emit(
                        ItemCompleted,
                        item=str(rel),
                        status="failed",
                        detail=str(exc),
                    )
                else:
                    print(f"  [ERROR] {rel}: {exc}", file=sys.stderr)
                continue
            if emitter is not None:
                emitter.emit(
                    PhaseCompleted,
                    phase="measure-grain",
                    item=str(rel),
                    status="succeeded",
                )
        try:
            backend = av1_backend.choose_backend(
                video,
                "auto",
                gpu_index,
                nvencc_ok=nvencc_ok,
                grain_score=grain.score if grain else None,
                grain_threshold=args.grain_threshold,
            )
            engine = av1_backend.choose_encode_engine(backend, video, nvencc_ok=nvencc_ok)
        except ValueError as exc:
            backend, engine = "?", f"error: {exc}"

        kept_audio, audio_fallback = langfilter.filter_audio(
            probed["audio"], audio_lang, single=single_audio_track
        )
        kept_subs = langfilter.filter_subtitles(probed["subtitles"], subtitle_lang)
        audio_desc = (
            ", ".join(
                f"{a['codec_name']}/{a['channels']}ch/{a.get('language') or 'und'} -> "
                f"opus@{presets.opus_bitrate_kbps(a['channels'])}k"
                for a in kept_audio
            )
            or "(none)"
        )
        dropped_audio = len(probed["audio"]) - len(kept_audio)
        dropped_subs = len(probed["subtitles"]) - len(kept_subs)

        print(f"  {rel}")
        print(
            f"      {video['width']}x{video['height']} ({tier}) {video['codec_name']} "
            f"{video.get('profile') or ''} {dynamic_range}  size={size_desc}"
        )
        print(f"      preset: {preset.name} -- {preset.description}")
        active_crf = presets.svt_crf(preset, svt_implementation)
        print(
            f"      cpu encoder: {svt_implementation.label}; "
            f"crf={active_crf if active_crf is not None else 'unavailable'}"
        )
        nvencc_label = "yes" if nvencc_ok else "no"
        print(f"      auto backend: {backend} via {engine}  (nvencc={nvencc_label})")
        if grain is not None:
            verdict = "cpu preferred" if grain.score >= args.grain_threshold else "clean"
            samples = ", ".join(f"{s:.4f}" for s in grain.samples)
            print(
                f"      grain: {grain.score:.4f} ({verdict}, threshold={args.grain_threshold:.4f}, "
                f"samples=[{samples}])"
            )
        elif args.no_grain_routing and gpu_index is not None:
            print("      grain: skipped (--no-grain-routing)")
        audio_line = f"      audio:  {audio_desc}"
        if audio_fallback:
            audio_line += " (fallback: no track matched)"
        print(audio_line)
        if dropped_audio:
            print(f"      audio-lang={audio_lang!r} drops {dropped_audio} track(s) not matching")
        subtitle_line = f"      subtitles kept: {len(kept_subs)}"
        if dropped_subs:
            subtitle_line += f" (drops {dropped_subs} not matching subtitle-lang={subtitle_lang!r})"
        print(subtitle_line)
        cover = run_mod.find_sidecar_cover(abs_path)
        print(f"      cover art: {cover if cover else '(none found)'}")
        if dv and engine == "nvencc":
            print("      note: Dolby Vision -- GPU path uses nvencc --dolby-vision-rpu copy")
            print("            (profile 10.1). Pass --backend cpu for libsvtav1 -dolbyvision.")
        elif dv and backend == "cpu":
            print("      note: Dolby Vision -- nvencc not found; auto uses cpu/libsvtav1")
            print("            so RPU is preserved. Install nvencc for GPU DV encodes.")
        elif hdr10_plus and engine == "nvencc":
            print("      note: HDR10+ -- GPU path uses nvencc --dhdr10-info copy")
        if emitter is not None:
            emitter.emit(ItemCompleted, item=str(rel), status="succeeded")

    if context is not None:
        succeeded = len(candidates) - errors
        context.record_outcome(
            errors=errors,
            status="partial" if errors and succeeded else ("failed" if errors else "succeeded"),
        )
    return 1 if errors else 0


def cmd_list_presets(args):
    del args
    implementation = detect_svt_implementation()
    print(f"Detected CPU encoder: {implementation.label}")
    for (tier, profile), preset in sorted(presets.PRESETS.items()):
        active_crf = presets.svt_crf(preset, implementation)
        print(f"{preset.name}  [{tier} / {profile}]")
        print(f"    {preset.description}")
        print(
            f"    cpu:   preset={preset.svt_preset} "
            f"crf(mainline)={preset.crf} crf(svt-av1-hdr)={preset.svt_hdr_crf} "
            f"active-crf={active_crf if active_crf is not None else 'unavailable'} "
            f"tune={preset.svt_tune} "
            f"film-grain={preset.film_grain} "
            f"film-grain-denoise={int(preset.film_grain_denoise)} extra={preset.svt_extra}"
        )
        print(
            f"    nvenc: preset={preset.nvenc_preset} tune={preset.nvenc_tune} "
            f"cq={preset.nvenc_cq} extra={preset.nvenc_extra}"
        )
    print(
        "\nHDR preserves 10-bit color and HDR metadata without changing the CRF/CQ quality target."
    )
    print(
        f"Output bitrate is additionally capped to {presets.MAX_BITRATE_FRACTION_OF_SOURCE:.0%} "
        "of each source file's own bitrate by default (--max-bitrate-fraction / --no-bitrate-cap)."
    )


def cmd_run(args):
    root = Path(args.root)
    cfg = config.load_config(args.env_file)

    audio_lang = _resolve(args.audio_lang, cfg.audio_lang)
    subtitle_lang = _resolve(args.subtitle_lang, cfg.subtitle_lang)
    output_dir_str = _resolve(args.output_dir, str(cfg.output_dir) if cfg.output_dir else None)
    output_dir = Path(output_dir_str) if output_dir_str else None
    if args.no_bitrate_cap:
        max_bitrate_fraction = None
    else:
        max_bitrate_fraction = _resolve(args.max_bitrate_fraction, cfg.max_bitrate_fraction)
    single_audio_track = not args.all_audio_tracks
    cover_image_path = Path(args.cover_image) if args.cover_image else None
    auto_cover_art = not args.no_cover_art

    if args.overwrite_existing and output_dir is None:
        raise config.ConfigError("--overwrite-existing requires --output-dir")
    if cover_image_path is not None and not cover_image_path.is_file():
        raise config.ConfigError(f"cover image is not a file: {cover_image_path}")

    backup_dir = (
        None
        if args.no_backup or output_dir is not None
        else str(_cache_path(root, "originals", args.backup_dir))
    )
    log_dir = _cache_path(root, "logs", args.log_dir)
    context: AppContext | None = getattr(args, "_context", None)
    emitter = (
        context.start_run(
            command="transcode run",
            root=root,
            root_source=getattr(args, "_root_source", None),
            mode="applied" if args.yes else "dry-run",
            wants_journal=args.yes,
        )
        if context is not None
        else None
    )

    gpu_index = None
    if args.backend in ("auto", "nvenc"):
        gpu_index = detect_av1_nvenc_gpu()
        if args.backend == "nvenc" and gpu_index is None:
            text = "No AV1-capable NVIDIA GPU detected; cannot honor --backend nvenc."
            if context is not None:
                context.message(text, level="error")
                context.record_outcome(status="failed", errors=1)
            else:
                print(text, file=sys.stderr)
            return 1

    if args.yes and backup_dir is None and output_dir is None:
        warning = (
            "Running with --yes --no-backup: originals will be permanently replaced "
            "without backups."
        )
        if context is not None:
            context.message(warning, level="warning")
        else:
            print(f"!! {warning}", file=sys.stderr)

    exclude_dirs = frozenset(
        p.resolve()
        for p in (output_dir, Path(backup_dir) if backup_dir else None, log_dir)
        if p is not None
    )

    if emitter is not None:
        emitter.emit(PhaseStarted, phase="discovery")
    discovery_started = time.monotonic()
    candidates = list(
        walk_media_files(
            root,
            DEFAULT_EXTENSIONS,
            path_filter=args.path,
            limit=args.limit,
            exclude_dirs=exclude_dirs,
        )
    )
    if emitter is not None:
        emitter.emit(
            PhaseCompleted,
            phase="discovery",
            status="succeeded",
            elapsed_seconds=time.monotonic() - discovery_started,
        )
    if output_dir is not None:
        collisions = _output_collisions(output_dir, candidates)
        if collisions:
            for destination, sources in sorted(collisions.items()):
                joined = ", ".join(str(source.relative_to(root)) for source in sources)
                text = f"multiple sources map to {destination}: {joined}"
                if context is not None:
                    context.message(text, level="error", phase="discovery")
                else:
                    print(f"[ERROR] {text}", file=sys.stderr)
            if context is not None:
                context.record_outcome(status="failed", errors=len(collisions))
            return 2

    changed = planned = errors = 0
    for index, abs_path in enumerate(candidates, start=1):
        rel = abs_path.relative_to(root)
        item_log_path = log_dir / rel.with_suffix(".log")
        if emitter is not None:
            emitter.emit(
                ItemStarted,
                item=str(rel),
                index=index,
                total=len(candidates),
                log_path=str(item_log_path) if args.yes else None,
            )

        phase_started: dict[str, float] = {}

        def _phase(
            phase: str,
            state: str,
            _phase_started: dict[str, float] = phase_started,
            _rel: Path = rel,
        ) -> None:
            if emitter is None:
                return
            if state == "started":
                _phase_started[phase] = time.monotonic()
                emitter.emit(PhaseStarted, phase=phase, item=str(_rel))
                return
            status = state if state in ("failed", "cancelled") else "succeeded"
            started = _phase_started.pop(phase, None)
            emitter.emit(
                PhaseCompleted,
                phase=phase,
                item=str(_rel),
                status=status,
                elapsed_seconds=time.monotonic() - started if started is not None else None,
            )

        def _progress(
            data: dict[str, float | str | None],
            _rel: Path = rel,
        ) -> None:
            if emitter is None:
                return
            fields = {
                key: data.get(key)
                for key in (
                    "percent",
                    "media_position",
                    "media_duration",
                    "fps",
                    "speed",
                    "eta_seconds",
                    "backend",
                )
            }
            emitter.emit(ItemProgress, item=str(_rel), phase="encode", **fields)

        def _heartbeat(phase: str, _rel: Path = rel) -> None:
            if emitter is not None:
                emitter.emit(RunHeartbeat, phase=phase, item=str(_rel), message="still running")

        try:
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
                audio_lang=audio_lang,
                subtitle_lang=subtitle_lang,
                single_audio_track=single_audio_track,
                max_bitrate_fraction=max_bitrate_fraction,
                output_dir=output_dir,
                cover_image_path=cover_image_path,
                auto_cover_art=auto_cover_art,
                overwrite_existing=args.overwrite_existing,
                on_progress=_progress if args.yes else None,
                on_phase=_phase,
                on_heartbeat=_heartbeat if args.yes else None,
                grain_routing=not args.no_grain_routing,
                grain_threshold=args.grain_threshold,
            )
        except KeyboardInterrupt, CancellationRequested:
            if emitter is not None:
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="cancelled",
                    log_path=str(item_log_path),
                )
            raise
        except RecoveryRequired as exc:
            if emitter is not None:
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="failed",
                    detail=str(exc),
                    log_path=str(item_log_path),
                )
            raise
        if result.status == "planned":
            planned += 1
            print(f"  {result.rel}")
            print(f"      $ {result.detail}")
            if emitter is not None:
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="skipped",
                    detail=result.detail,
                )
        elif result.status == "changed":
            changed += 1
            print(f"  [OK] {result.rel}  ({result.detail})")
            if emitter is not None:
                final_path = (
                    (output_dir / abs_path.name).with_suffix(".mkv")
                    if output_dir is not None
                    else abs_path.with_suffix(".mkv")
                )
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="succeeded",
                    detail=result.detail,
                    output=str(final_path),
                    log_path=str(item_log_path),
                    size_bytes=final_path.stat().st_size if final_path.is_file() else None,
                )
        elif result.status == "error":
            errors += 1
            if context is not None:
                context.message(result.detail, level="error", item=str(rel))
            else:
                print(f"  [ERROR] {result.rel}: {result.detail}", file=sys.stderr)
            if emitter is not None:
                emitter.emit(
                    ItemCompleted,
                    item=str(rel),
                    status="failed",
                    detail=result.detail,
                    log_path=str(item_log_path) if args.yes else None,
                )

    mode = "APPLIED" if args.yes else "DRY RUN (pass --yes to execute for real)"
    print(f"\n[{mode}] changed={changed} planned={planned} errors={errors}")
    if args.yes and output_dir:
        print(f"Converted files written under: {output_dir}  (originals untouched)")
    elif args.yes and backup_dir and changed:
        print(f"Backups of changed files were retained under: {backup_dir}")
    if args.yes:
        print(f"Per-file live logs under: {log_dir}  (tail -f <file> to watch progress in full)")
    exit_code = 1 if errors else 0
    if context is not None:
        status = "succeeded" if exit_code == 0 else ("partial" if changed or planned else "failed")
        context.record_outcome(
            status=status,
            changed=changed,
            planned=planned,
            errors=errors,
        )
    return exit_code


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
            command="transcode purge-backups",
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
        print(f"Would permanently delete {backup_dir} ({human_size(size)}).")
        print("Re-run with --yes to confirm.")
        return 0
    shutil.rmtree(backup_dir)
    print(f"Deleted {backup_dir} ({human_size(size)} freed).")
    if context is not None:
        context.record_outcome(changed=1)
    return 0


def _add_grain_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--grain-threshold",
        type=_grain_threshold,
        default=GRAIN_CPU_THRESHOLD,
        help="Grain/noise score (1 - denoise-diff SSIM, see medialib.grain and "
        f"reference/presets.md) at or above which --backend auto prefers cpu over nvenc "
        f"even when a GPU is available (default: {GRAIN_CPU_THRESHOLD} -- provisional, "
        "calibrated against only a handful of real titles so far). Only checked when a "
        "GPU is present and Dolby Vision isn't already forcing cpu regardless.",
    )
    sp.add_argument(
        "--no-grain-routing",
        action="store_true",
        help="Don't measure per-file grain/noise at all -- --backend auto falls back to its "
        "pre-grain behavior (nvenc whenever a GPU is available; DV/HDR10+ rules unchanged)",
    )


def _add_language_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--audio-lang",
        default=None,
        help="Keep only audio tracks matching this language (ISO 639-2, e.g. 'eng'; "
        "or 'all' to keep every track). Default: 'eng', or TRANSCODE_AUDIO_LANG "
        "from .env. Falls back to keeping every track if none match (never produces "
        "a silent file).",
    )
    sp.add_argument(
        "--subtitle-lang",
        default=None,
        help="Keep only subtitle tracks matching this language (or 'all' for every "
        "track). Default: 'eng', or TRANSCODE_SUBTITLE_LANG from .env.",
    )


def build_parser(default_root: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="psammophis transcode",
        description="AV1 (libsvtav1/av1_nvenc) + Opus transcode toolkit",
    )
    p.add_argument("--root", default=default_root, help="Media library root")
    p.add_argument(
        "--env-file",
        default=".env",
        help="Path to an optional .env config file (TRANSCODE_* keys; see .env.example). "
        "Real environment variables always override it; explicit CLI flags override both.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "probe",
        help="Read-only: report resolution/HDR/DV/audio/subtitles and which preset+backend "
        "`run` would pick",
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
    _add_language_args(sp)
    sp.add_argument(
        "--all-audio-tracks",
        action="store_true",
        help="Preview keeping every matching-language audio track instead of just the single "
        "highest-quality one",
    )
    _add_grain_args(sp)
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
        help="Encoder backend (default: auto -- nvenc/GPU if an AV1-capable NVIDIA GPU is "
        "found, else cpu/libsvtav1). Dolby Vision and HDR10+ on GPU use nvencc (rigaya "
        "NVEnc with libdovi) so RPU/dynamic metadata is preserved; without nvencc, DV "
        "falls back to cpu. Plain SDR/HDR10 still uses ffmpeg av1_nvenc.",
    )
    sp.add_argument(
        "--yes",
        action="store_true",
        help="Actually execute (default is a dry run that prints the ffmpeg command it would run)",
    )
    sp.add_argument(
        "--output-dir",
        default=None,
        help="Write converted files directly into this directory under their own filename "
        "(flat -- not mirroring each file's path relative to --root) instead of swapping in "
        "place. When set, the original source is never touched -- no backup/delete happens "
        "at all. A destination filename that already exists is left alone and reported as "
        "an error unless --overwrite-existing is passed. Default: TRANSCODE_OUTPUT_DIR "
        "from .env, or in-place if neither is set.",
    )
    sp.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="When --output-dir is set and the destination filename already exists, replace "
        "it instead of bailing out with an error (default: refuse and leave the existing "
        "file alone)",
    )
    sp.add_argument(
        "--no-backup",
        action="store_true",
        help="Replace originals without retaining backups",
    )
    sp.add_argument(
        "--backup-dir",
        help="Where to retain originals as backups (default: "
        "<root>/.cache/transcode/originals) "
        "-- ignored when --output-dir is set",
    )
    sp.add_argument(
        "--log-dir",
        help="Where to write per-file encoder logs (default: <root>/.cache/transcode/logs)",
    )
    sp.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Drop subtitle/attachment streams instead of copying them (use if the source "
        "container's subtitle codec can't mux into Matroska)",
    )
    _add_language_args(sp)
    sp.add_argument(
        "--all-audio-tracks",
        action="store_true",
        help="Keep and transcode every audio track matching --audio-lang instead of just the "
        "single highest-quality one (default: pick one -- e.g. a TrueHD Atmos track over a "
        "same-language E-AC3 'compatibility' copy of the same mix, a real pattern in remuxes "
        "with more than one delivery of the same audio)",
    )
    bitrate_cap = sp.add_mutually_exclusive_group()
    bitrate_cap.add_argument(
        "--max-bitrate-fraction",
        type=_bitrate_fraction,
        default=None,
        help=f"Cap output video bitrate to this fraction of the source's own bitrate "
        f"(default: {presets.MAX_BITRATE_FRACTION_OF_SOURCE}, or "
        "TRANSCODE_MAX_BITRATE_FRACTION from .env) -- the safety net against producing "
        "a file larger than the source on already-efficiently-encoded input.",
    )
    bitrate_cap.add_argument(
        "--no-bitrate-cap",
        action="store_true",
        help="Disable the source-relative bitrate ceiling entirely (pure CRF/CQ, no maximum)",
    )
    _add_grain_args(sp)
    sp.add_argument(
        "--cover-image",
        default=None,
        help="Embed this image as Matroska cover art (a proper attachment, not a video stream) "
        "instead of auto-detecting one",
    )
    sp.add_argument(
        "--no-cover-art",
        action="store_true",
        help="Don't look for or embed a poster.jpg/cover.jpg/folder.jpg sitting next to the "
        "source (default: auto-embed one if found, e.g. one artwork already fetched)",
    )
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser(
        "purge-backups", help="Permanently delete the backup directory of originals"
    )
    sp.add_argument("--backup-dir", default=None)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_purge_backups)

    return p


def main(argv=None, context: AppContext | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    default = resolve_default_root(feature_env="TRANSCODE_ROOT")
    parser = build_parser(str(default.path))
    args = parser.parse_args(raw)
    if args.command in ("probe", "run", "purge-backups"):
        try:
            root = validate_root(args.root)
        except RootError as exc:
            print(f"Invalid media root: {exc}", file=sys.stderr)
            return 2
        args.root = str(root)
    args._root_source = root_option_source(raw, default)
    if args.command == "purge-backups" and args.backup_dir is None:
        args.backup_dir = str(_cache_path(Path(args.root), "originals"))
    args._context = context
    try:
        result = args.func(args)
    except config.ConfigError as exc:
        text = f"Configuration error: {exc}"
        if context is not None:
            context.message(text, level="error")
            context.record_outcome(status="failed", errors=1)
        else:
            print(text, file=sys.stderr)
        return 2
    return 0 if result is None else int(result)


if __name__ == "__main__":
    main()
