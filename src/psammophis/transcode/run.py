"""Per-file execution: pick a backend, run ffmpeg with a live-streamed and
persisted log (an AV1 encode runs minutes to hours, not the seconds a
track-strip stream-copy takes, so silently buffering output until exit
like transcode.py does isn't acceptable here), verify the result, and swap it
in behind a backup. Same temp-file -> verify -> backup -> swap shape as
track-strip's apply.py, just built for a much longer-running job.
"""

import contextlib
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from psammophis.medialib import av1_backend, colorinfo
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.grain import GrainMeasurement, measure_grain
from psammophis.medialib.svt import SvtImplementation, detect_svt_implementation
from psammophis.medialib.videoprobe import probe_file
from psammophis.runtime.filesystem import (
    RecoveryRequired,
    discard_staged_backup,
    fsync_directory,
    install_no_replace,
    install_verified,
    installation_completed,
    path_exists,
    stage_backup,
)
from psammophis.runtime.signals import CancellationRequested

from . import command as command_mod
from . import langfilter, nvencc_cmd

DECODE_SPOT_CHECK_SECONDS = 3

StructuredProgressCallback = Callable[[dict[str, float | str | None]], None]
PhaseCallback = Callable[[str, str], None]
HeartbeatCallback = Callable[[str], None]

# Conventional poster/cover filenames -- checked in this order next to the
# source video. "poster.jpg" is what artwork itself leaves
# behind once a movie is identified (see
# skills/organize/references/naming-conventions.md); the rest are the
# same file under Plex/Jellyfin/Kodi's other common names. No network
# lookup happens here -- transcode doesn't gain a TMDB dependency for
# this, it only ever uses whatever's already sitting on disk.
_COVER_ART_FILENAMES = (
    "poster.jpg",
    "poster.png",
    "cover.jpg",
    "cover.png",
    "folder.jpg",
    "folder.png",
)


def find_sidecar_cover(video_path: Path) -> Path | None:
    for name in _COVER_ART_FILENAMES:
        candidate = video_path.parent / name
        if candidate.is_file():
            return candidate
    return None


class TranscodeResult:
    def __init__(self, rel: str, status: str, detail: str = ""):
        self.rel = rel
        self.status = status  # "changed" | "planned" | "unchanged" | "error"
        self.detail = detail


def stream_process(
    cmd: list[str],
    log_path: Path,
    total_duration: float | None,
    on_progress: StructuredProgressCallback | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    min_progress_interval: float = 1.0,
) -> tuple[int, str]:
    """Run `cmd`, writing every output line to `log_path` in real time
    (so `tail -f log_path` gives full-fidelity live monitoring regardless of
    how this function itself is invoked) while forwarding a throttled subset
    to `on_progress`.

    Uses the shared process supervisor so stdout and stderr are drained
    concurrently (FFmpeg ``-progress pipe:1`` uses stdout; diagnostics stay
    on stderr). NVEncC progress lines on either stream are parsed when present.
    """
    from psammophis.runtime.process import (
        ProcessSupervisor,
        make_ffmpeg_progress_parser,
        parse_nvencc_progress_line,
    )

    is_ffmpeg = bool(cmd) and Path(cmd[0]).name.startswith("ffmpeg")
    ffmpeg_parser = make_ffmpeg_progress_parser(total_duration) if is_ffmpeg else None

    def progress_parser(stream_name: str, line: str) -> dict[str, float | str | None] | None:
        if ffmpeg_parser is not None and stream_name == "stdout":
            return ffmpeg_parser(stream_name, line)
        return parse_nvencc_progress_line(stream_name, line)

    def on_structured(parsed: dict[str, float | str | None]) -> None:
        if on_progress is None:
            return
        parsed.setdefault("backend", "ffmpeg" if is_ffmpeg else "nvencc")
        on_progress(parsed)

    result = ProcessSupervisor(
        cmd,
        log_path=log_path,
        progress_parser=progress_parser if on_progress is not None else None,
        on_progress=on_structured if on_progress is not None else None,
        on_heartbeat=on_heartbeat,
        min_progress_interval=min_progress_interval,
    ).run()
    return result.returncode, result.tail


def _decode_spot_check(path: Path, seconds: int = DECODE_SPOT_CHECK_SECONDS) -> tuple[bool, str]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-t",
        str(seconds),
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or proc.stderr.strip():
        detail = proc.stderr.strip()[:300] or str(proc.returncode)
        return False, f"decode error near start: {detail}"
    return True, "ok"


def verify_output(original_probed: dict, new_path: Path) -> tuple[bool, str]:
    new_probed = probe_file(new_path)
    if new_probed.get("video") is None:
        return False, "output has no video stream"
    if not new_probed.get("audio"):
        return False, "output has no audio stream"

    orig_dur = original_probed["format"].get("duration")
    new_dur = new_probed["format"].get("duration")
    if orig_dur and new_dur:
        tolerance = max(2.0, orig_dur * 0.02)
        if abs(orig_dur - new_dur) > tolerance:
            return False, f"duration mismatch: {orig_dur:.1f}s -> {new_dur:.1f}s"

    orig_video = original_probed.get("video") or {}
    new_video = new_probed["video"]
    if not _has_measured_codec_statistics(new_video):
        return False, "output AV1 stream is missing measured track statistics"
    stale_video = _copied_codec_statistics(orig_video, new_video)
    if stale_video:
        names = ", ".join(sorted(stale_video))
        return False, f"output AV1 stream carries stale source statistics: {names}"
    stale_audio = [
        i
        for i, stream in enumerate(new_probed.get("audio", []))
        if any(
            _copied_codec_statistics(source, stream) for source in original_probed.get("audio", [])
        )
    ]
    if stale_audio:
        indexes = ", ".join(str(i) for i in stale_audio)
        return False, f"output Opus stream(s) carry stale source statistics: {indexes}"
    missing_audio = [
        i
        for i, stream in enumerate(new_probed.get("audio", []))
        if not _has_measured_codec_statistics(stream)
    ]
    if missing_audio:
        indexes = ", ".join(str(i) for i in missing_audio)
        return False, f"output Opus stream(s) are missing measured track statistics: {indexes}"
    if colorinfo.has_dolby_vision(orig_video) and not colorinfo.has_dolby_vision(new_video):
        return False, "source had Dolby Vision but output is missing DOVI configuration record"
    # Checked regardless of Dolby Vision presence -- nvencc's DV path used to
    # leave these unset even with RPU/DOVI config intact (see
    # reference/incidents.md), which this check would have caught if it
    # hadn't been carved out for exactly that reason. nvencc_cmd.py now sets
    # them explicitly (colorinfo.nvencc_hdr_args), so there's no longer a
    # reason a passing DV output should be missing them.
    if colorinfo.is_hdr(orig_video):
        if not colorinfo.is_hdr(new_video):
            return False, "source was HDR but output lost its PQ/HLG transfer characteristic"
        if orig_video.get("mastering_display") and not new_video.get("mastering_display"):
            return False, "source had mastering-display metadata but output is missing it"
    if colorinfo.has_hdr10_plus(orig_video) and not colorinfo.has_hdr10_plus(new_video):
        return (
            False,
            "source had HDR10+ dynamic metadata but output is missing it (ffprobe side_data)",
        )

    ok, detail = _decode_spot_check(new_path)
    if not ok:
        return False, detail

    orig_size = original_probed["format"].get("size")
    new_size = new_probed["format"].get("size")
    if orig_size and new_size:
        reduction = (1 - new_size / orig_size) * 100
        orig_bitrate = _overall_bitrate_mbps(original_probed)
        new_bitrate = _overall_bitrate_mbps(new_probed)
        bitrate_detail = (
            f"; overall bitrate {orig_bitrate:.2f} -> {new_bitrate:.2f} Mb/s"
            if orig_bitrate is not None and new_bitrate is not None
            else ""
        )
        size_detail = (
            f"{orig_size / 1024**3:.2f} -> {new_size / 1024**3:.2f} GiB "
            f"({reduction:.1f}% smaller){bitrate_detail}"
        )
        if new_size > orig_size:
            return True, f"ok (warning: {size_detail} -- output is larger!)"
        return True, f"ok ({size_detail})"
    return True, "ok (size/bitrate unavailable)"


def _overall_bitrate_mbps(probed: dict) -> float | None:
    fmt = probed.get("format") or {}
    bit_rate = fmt.get("bit_rate")
    if bit_rate:
        return bit_rate / 1_000_000
    size = fmt.get("size")
    duration = fmt.get("duration")
    if size and duration:
        return size * 8 / duration / 1_000_000
    return None


def _copied_codec_statistics(source_stream: dict, output_stream: dict) -> set[str]:
    """Identify source-derived byte/rate/frame counters copied verbatim onto
    a transcoded stream. DURATION and NUMBER_OF_FRAMES are intentionally
    excluded: a correct transcode naturally preserves both."""
    source_tags = source_stream.get("statistics_tags") or {}
    output_tags = output_stream.get("statistics_tags") or {}
    codec_prefixes = ("BPS", "NUMBER_OF_BYTES")
    copied: set[str] = set()
    for prefix in codec_prefixes:
        source_values = {
            value for key, value in source_tags.items() if key.upper().startswith(prefix)
        }
        for key, value in output_tags.items():
            if key.upper().startswith(prefix) and value in source_values:
                copied.add(key)
    return copied


def _has_measured_codec_statistics(stream: dict) -> bool:
    tags = stream.get("statistics_tags") or {}
    return any(key.upper().startswith("BPS") for key in tags) and any(
        key.upper().startswith("NUMBER_OF_BYTES") for key in tags
    )


def _run_postprocess(
    cmd: list[str],
    *,
    timeout: float,
    on_heartbeat: Callable[[], None] | None = None,
):
    from psammophis.runtime.process import ProcessSupervisor

    return ProcessSupervisor(
        cmd,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    ).run()


def _attach_cover_remux(
    video_path: Path,
    cover_image_path: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> None:
    """NVEncC does not take our Matroska -attach path; remux with ffmpeg to add cover."""
    tmp = video_path.with_name(video_path.stem + ".cover-tmp.mkv")
    if path_exists(tmp):
        raise FileExistsError(f"cover-art temporary file already exists: {tmp}")
    probed = probe_file(video_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-i",
        str(video_path),
        "-map",
        "0",
        "-c",
        "copy",
        *command_mod.cover_art_args(cover_image_path, probed.get("attachment_count", 0)),
        str(tmp),
    ]
    result = _run_postprocess(cmd, timeout=600, on_heartbeat=on_heartbeat)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"cover-art remux failed ({result.returncode}): {result.tail[-500:]}")
    os.replace(tmp, video_path)
    fsync_directory(video_path.parent)


def _refresh_track_statistics(
    video_path: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> None:
    """Measure the encoded tracks and write accurate Matroska BPS/duration/
    frame/byte counters without remuxing the media payload."""
    cmd = ["mkvpropedit", str(video_path), "--add-track-statistics-tags"]
    result = _run_postprocess(cmd, timeout=3600, on_heartbeat=on_heartbeat)
    if result.returncode != 0:
        raise RuntimeError(
            f"track-statistics refresh failed ({result.returncode}): {result.tail[-500:]}"
        )


def build_encode_command(
    abs_path: Path,
    output_path: Path,
    probed: dict,
    preset: presets.Preset,
    backend: str,
    engine: str,
    gpu_index: int | None,
    drop_subtitles: bool,
    audio_lang: str,
    subtitle_lang: str,
    single_audio_track: bool,
    max_bitrate_fraction: float | None,
    cover_image_path: Path | None,
    svt_implementation: SvtImplementation | None = None,
) -> list[str]:
    """Dispatch to ffmpeg (command.py) or nvencc (nvencc_cmd.py).

    Cover art is applied inside the ffmpeg command when engine is ffmpeg; for
    nvencc the caller runs `_attach_cover_remux` after a successful encode.
    """
    if engine == "nvencc":
        if gpu_index is None:
            raise ValueError("nvencc encode requires a GPU index")
        return nvencc_cmd.build_nvencc_command(
            abs_path,
            output_path,
            probed,
            preset,
            gpu_index=gpu_index,
            drop_subtitles=drop_subtitles,
            audio_lang=audio_lang,
            subtitle_lang=subtitle_lang,
            single_audio_track=single_audio_track,
            max_bitrate_fraction=max_bitrate_fraction,
        )
    return command_mod.build_command(
        abs_path,
        output_path,
        probed,
        preset,
        backend,
        gpu_index=gpu_index,
        drop_subtitles=drop_subtitles,
        audio_lang=audio_lang,
        subtitle_lang=subtitle_lang,
        single_audio_track=single_audio_track,
        max_bitrate_fraction=max_bitrate_fraction,
        cover_image_path=cover_image_path,
        svt_implementation=svt_implementation,
    )


def _emit_phase(callback: PhaseCallback | None, phase: str, state: str) -> None:
    if callback is not None:
        callback(phase, state)


def _commit_in_place(
    source: Path,
    temporary: Path,
    final_path: Path,
    backup_dir: str | None,
    relative_path: Path,
    on_phase: PhaseCallback | None = None,
) -> None:
    """Install verified output without creating an unrecoverable delete gap."""
    if final_path != source and path_exists(final_path):
        raise FileExistsError(f"{final_path} already exists, refusing to overwrite")
    backup_path = Path(backup_dir) / relative_path if backup_dir is not None else None
    backup_staged = False
    active_phase: str | None = None
    try:
        if backup_path is not None:
            active_phase = "backup"
            _emit_phase(on_phase, active_phase, "started")
            stage_backup(source, backup_path)
            backup_staged = True
            _emit_phase(on_phase, active_phase, "succeeded")
        active_phase = "commit"
        _emit_phase(on_phase, active_phase, "started")
        install_verified(source, temporary, final_path)
        _emit_phase(on_phase, active_phase, "succeeded")
        active_phase = None
    except BaseException as exc:
        if (
            backup_path is not None
            and backup_staged
            and not installation_completed(source, temporary, final_path)
        ):
            discard_staged_backup(backup_path)
        if active_phase is not None:
            state = (
                "cancelled"
                if isinstance(exc, (KeyboardInterrupt, SystemExit, CancellationRequested))
                else "failed"
            )
            with contextlib.suppress(Exception):
                _emit_phase(on_phase, active_phase, state)
        raise


def transcode_one(
    abs_path: Path,
    root: Path,
    profile: str,
    backend_pref: str,
    gpu_index: int | None,
    backup_dir: str | None,
    execute: bool,
    log_dir: Path,
    drop_subtitles: bool = False,
    audio_lang: str = langfilter.ALL,
    subtitle_lang: str = langfilter.ALL,
    single_audio_track: bool = True,
    max_bitrate_fraction: float | None = presets.MAX_BITRATE_FRACTION_OF_SOURCE,
    output_dir: Path | None = None,
    cover_image_path: Path | None = None,
    auto_cover_art: bool = True,
    overwrite_existing: bool = False,
    on_progress: StructuredProgressCallback | None = None,
    on_phase: PhaseCallback | None = None,
    on_heartbeat: HeartbeatCallback | None = None,
    grain_routing: bool = True,
    grain_threshold: float = av1_backend.GRAIN_CPU_THRESHOLD,
) -> tuple[TranscodeResult, dict | None]:
    """When `output_dir` is set, the converted file is written directly into
    that directory under its own filename (flat -- not mirroring `abs_path`'s
    directory structure relative to `root`) and the original source is left
    completely untouched -- no backup/delete step at all, since nothing
    about the source changed. A destination filename that already exists is
    left alone and reported as an error unless `overwrite_existing` is set.
    `backup_dir` only applies to the default in-place mode, where the source
    *is* replaced.

    Cover art: `cover_image_path` forces a specific image; otherwise, if
    `auto_cover_art` (the default), `find_sidecar_cover` looks for a
    conventional poster/cover file next to `abs_path` and uses that if
    found. Neither happening (no override, nothing found, or
    `auto_cover_art=False`) just means no cover is embedded -- never an
    error."""
    rel = abs_path.relative_to(root)
    resolved_cover = cover_image_path or (find_sidecar_cover(abs_path) if auto_cover_art else None)
    final_path = (
        (output_dir / abs_path.name).with_suffix(".mkv")
        if output_dir
        else abs_path.with_suffix(".mkv")
    )

    if output_dir is not None and final_path.resolve(strict=False) == abs_path.resolve(
        strict=False
    ):
        return (
            TranscodeResult(
                str(rel),
                "error",
                "--output-dir maps the converted file onto its own source; choose a "
                "different directory",
            ),
            None,
        )

    destination_collision = path_exists(final_path) and final_path != abs_path
    if destination_collision and (output_dir is None or not overwrite_existing):
        return (
            TranscodeResult(
                str(rel),
                "error",
                f"destination already exists: {final_path} "
                + (
                    "(pass --overwrite-existing to replace it)"
                    if output_dir is not None
                    else "(in-place transcoding never overwrites a different existing file)"
                ),
            ),
            None,
        )
    if execute and backup_dir is not None:
        backup_path = Path(backup_dir) / rel
        if path_exists(backup_path):
            return (
                TranscodeResult(
                    str(rel),
                    "error",
                    f"backup already exists, refusing to overwrite: {backup_path}",
                ),
                None,
            )

    _emit_phase(on_phase, "probe", "started")
    try:
        probed = probe_file(abs_path)
    except Exception as exc:
        _emit_phase(on_phase, "probe", "failed")
        return TranscodeResult(str(rel), "error", f"probe failed: {exc}"), None
    _emit_phase(on_phase, "probe", "succeeded")

    video = probed.get("video")
    if video is None:
        return TranscodeResult(str(rel), "error", "no video stream found"), probed

    nvencc_ok = av1_backend.nvencc_available()
    grain: GrainMeasurement | None = None
    if grain_routing and av1_backend.grain_routing_applies(
        backend_pref, video, gpu_index, nvencc_ok
    ):
        _emit_phase(on_phase, "measure-grain", "started")
        try:
            grain = measure_grain(abs_path, probed["format"].get("duration"))
        except Exception as exc:
            _emit_phase(on_phase, "measure-grain", "failed")
            return TranscodeResult(str(rel), "error", f"grain measurement failed: {exc}"), probed
        _emit_phase(on_phase, "measure-grain", "succeeded")
    _emit_phase(on_phase, "select-backend", "started")
    try:
        backend = av1_backend.choose_backend(
            video,
            backend_pref,
            gpu_index,
            nvencc_ok=nvencc_ok,
            grain_score=grain.score if grain else None,
            grain_threshold=grain_threshold,
        )
        engine = av1_backend.choose_encode_engine(backend, video, nvencc_ok=nvencc_ok)
    except ValueError as exc:
        _emit_phase(on_phase, "select-backend", "failed")
        return TranscodeResult(str(rel), "error", str(exc)), probed
    _emit_phase(on_phase, "select-backend", "succeeded")

    hdr = colorinfo.is_hdr(video)
    preset = presets.select_preset(video["height"], profile, hdr)
    svt_implementation = detect_svt_implementation() if backend == "cpu" else None
    if not execute:
        cmd = build_encode_command(
            abs_path,
            final_path,
            probed,
            preset,
            backend,
            engine,
            gpu_index=gpu_index,
            drop_subtitles=drop_subtitles,
            audio_lang=audio_lang,
            subtitle_lang=subtitle_lang,
            single_audio_track=single_audio_track,
            max_bitrate_fraction=max_bitrate_fraction,
            cover_image_path=resolved_cover if engine == "ffmpeg" else None,
            svt_implementation=svt_implementation,
        )
        detail = " ".join(str(c) for c in cmd)
        if svt_implementation is not None:
            detail = f"[svt={svt_implementation.label}] {detail}"
        if engine == "nvencc" and resolved_cover is not None:
            detail += f" && ffmpeg-cover-attach {resolved_cover}"
        if grain is not None:
            detail = f"[grain={grain.score:.4f}] {detail}"
        return TranscodeResult(str(rel), "planned", detail), probed

    if shutil.which("mkvpropedit") is None:
        return (
            TranscodeResult(
                str(rel),
                "error",
                "mkvpropedit is required to write accurate output track statistics",
            ),
            probed,
        )

    # Written inside the actual target directory (output_dir when set, else
    # the source's own directory for in-place mode) -- not tucked next to the
    # source regardless of where the result is headed, which is confusing to
    # find and, with --output-dir pointed elsewhere, puts the in-progress file
    # nowhere near where anyone watching the destination would look for it.
    tmp_dir = output_dir if output_dir is not None else abs_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f".{abs_path.stem}.transcode-tmp.mkv"
    if path_exists(tmp_path):
        return (
            TranscodeResult(
                str(rel),
                "error",
                f"temporary work file already exists: {tmp_path}; inspect or remove it "
                "before retrying",
            ),
            probed,
        )

    log_path = log_dir / rel.with_suffix(".log")

    try:
        cmd = build_encode_command(
            abs_path,
            tmp_path,
            probed,
            preset,
            backend,
            engine,
            gpu_index=gpu_index,
            drop_subtitles=drop_subtitles,
            audio_lang=audio_lang,
            subtitle_lang=subtitle_lang,
            single_audio_track=single_audio_track,
            max_bitrate_fraction=max_bitrate_fraction,
            cover_image_path=resolved_cover if engine == "ffmpeg" else None,
            svt_implementation=svt_implementation,
        )
        _emit_phase(on_phase, "encode", "started")
        try:
            returncode, tail = stream_process(
                cmd,
                log_path,
                probed["format"].get("duration"),
                on_progress=on_progress,
                on_heartbeat=(lambda: on_heartbeat("encode")) if on_heartbeat else None,
            )
        except KeyboardInterrupt, SystemExit, CancellationRequested:
            _emit_phase(on_phase, "encode", "cancelled")
            raise
        except Exception:
            _emit_phase(on_phase, "encode", "failed")
            raise
        if returncode != 0:
            _emit_phase(on_phase, "encode", "failed")
            tmp_path.unlink(missing_ok=True)
            tool = "nvencc" if engine == "nvencc" else "ffmpeg"
            return (
                TranscodeResult(str(rel), "error", f"{tool} failed ({returncode}): {tail[-1000:]}"),
                probed,
            )
        _emit_phase(on_phase, "encode", "succeeded")

        if engine == "nvencc" and resolved_cover is not None:
            _emit_phase(on_phase, "cover", "started")
            try:
                _attach_cover_remux(
                    tmp_path,
                    resolved_cover,
                    (lambda: on_heartbeat("cover")) if on_heartbeat else None,
                )
            except KeyboardInterrupt, SystemExit, CancellationRequested:
                _emit_phase(on_phase, "cover", "cancelled")
                raise
            except Exception:
                _emit_phase(on_phase, "cover", "failed")
                raise
            _emit_phase(on_phase, "cover", "succeeded")

        _emit_phase(on_phase, "statistics", "started")
        try:
            _refresh_track_statistics(
                tmp_path,
                (lambda: on_heartbeat("statistics")) if on_heartbeat else None,
            )
        except KeyboardInterrupt, SystemExit, CancellationRequested:
            _emit_phase(on_phase, "statistics", "cancelled")
            raise
        except Exception:
            _emit_phase(on_phase, "statistics", "failed")
            raise
        _emit_phase(on_phase, "statistics", "succeeded")

        _emit_phase(on_phase, "verify", "started")
        try:
            ok, detail = verify_output(probed, tmp_path)
        except KeyboardInterrupt, SystemExit, CancellationRequested:
            _emit_phase(on_phase, "verify", "cancelled")
            raise
        except Exception:
            _emit_phase(on_phase, "verify", "failed")
            raise
        if not ok:
            _emit_phase(on_phase, "verify", "failed")
            tmp_path.unlink(missing_ok=True)
            return TranscodeResult(str(rel), "error", f"verification failed: {detail}"), probed
        _emit_phase(on_phase, "verify", "succeeded")

        if output_dir is not None:
            # Re-checked here (in addition to the early check above) in case
            # something created final_path during the encode itself -- a
            # multi-hour window is plenty of time for that race.
            if path_exists(final_path) and not overwrite_existing:
                tmp_path.unlink(missing_ok=True)
                return (
                    TranscodeResult(
                        str(rel),
                        "error",
                        f"destination already exists: {final_path} "
                        "(pass --overwrite-existing to replace it)",
                    ),
                    probed,
                )
            final_path.parent.mkdir(parents=True, exist_ok=True)
            _emit_phase(on_phase, "commit", "started")
            try:
                if overwrite_existing:
                    os.replace(tmp_path, final_path)
                    fsync_directory(final_path.parent)
                else:
                    install_no_replace(tmp_path, final_path)
            except KeyboardInterrupt, SystemExit, CancellationRequested:
                _emit_phase(on_phase, "commit", "cancelled")
                raise
            except Exception:
                _emit_phase(on_phase, "commit", "failed")
                raise
            _emit_phase(on_phase, "commit", "succeeded")
        else:
            _commit_in_place(
                abs_path,
                tmp_path,
                final_path,
                backup_dir,
                rel,
                on_phase,
            )
        label = f"{engine}/{backend}/{preset.name}"
        if svt_implementation is not None:
            label += f"/{svt_implementation.label}/crf{presets.svt_crf(preset, svt_implementation)}"
        if grain is not None:
            label += f" (grain={grain.score:.4f})"
        return TranscodeResult(str(rel), "changed", f"{label}: {detail}"), probed
    except KeyboardInterrupt, SystemExit, CancellationRequested:
        if path_exists(abs_path) or output_dir is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    except RecoveryRequired:
        raise
    except Exception as exc:
        if output_dir is not None or path_exists(abs_path):
            tmp_path.unlink(missing_ok=True)
        return TranscodeResult(str(rel), "error", str(exc)), probed
