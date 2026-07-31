"""Per-file execution: pick a backend, run ffmpeg with a live-streamed and
persisted log (an AV1 encode runs minutes to hours, not the seconds a
track-strip stream-copy takes, so silently buffering output until exit
like transcode.py does isn't acceptable here), verify the result, and swap it
in behind a backup. Same temp-file -> verify -> backup -> swap shape as
track-strip's apply.py, just built for a much longer-running job.
"""

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from medialib import av1_backend, colorinfo
from medialib import av1_presets as presets
from medialib.grain import GrainMeasurement, measure_grain
from medialib.svt import SvtImplementation, detect_svt_implementation
from medialib.videoprobe import probe_file

from . import command as command_mod
from . import langfilter, nvencc_cmd

DECODE_SPOT_CHECK_SECONDS = 3
DEFAULT_PROGRESS_INTERVAL = 10.0  # seconds between throttled progress lines to stdout

# Conventional poster/cover filenames -- checked in this order next to the
# source video. "poster.jpg" is what artwork itself leaves
# behind once a movie is identified (see
# skills/organize/references/naming-conventions.md); the rest are the
# same file under Plex/Jellyfin/Kodi's other common names. No network
# lookup happens here -- av1-transcode doesn't gain a TMDB dependency for
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


_PROGRESS_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.\d+.*speed=\s*([\d.]+)x")
# ffmpeg's periodic stats line always starts this way, even before it has a
# resolved time/speed to report (early frames print "time=N/A speed=N/A"
# while the encoder's lookahead buffer fills) -- checked separately from
# _PROGRESS_RE so those early not-yet-resolved ticks still count as ticks for
# throttling purposes instead of bypassing it. A real 4K HDR/Dolby Vision clip
# from this library spent its first ~10s emitting exactly such N/A lines
# every 0.5s, which -- before this was split out -- slipped past the
# throttle entirely since _PROGRESS_RE didn't match them.
_TICK_PREFIX = "frame="


class TranscodeResult:
    def __init__(self, rel: str, status: str, detail: str = ""):
        self.rel = rel
        self.status = status  # "changed" | "planned" | "unchanged" | "error"
        self.detail = detail


def _iter_ffmpeg_lines(stream: IO[str]):
    """ffmpeg separates its periodic stats updates with `\\r` (meant to
    overwrite the previous update on a real terminal) and everything else
    with `\\n` -- both are treated as line breaks here since every update,
    tick or not, gets its own row in the persisted log file."""
    buf = ""
    while True:
        ch = stream.read(1)
        if ch == "":
            break
        if ch in ("\r", "\n"):
            if buf:
                yield buf
                buf = ""
        else:
            buf += ch
    if buf:
        yield buf


def _format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _annotate_progress(line: str, total_duration: float | None) -> str:
    match = _PROGRESS_RE.search(line)
    if not match or not total_duration or total_duration <= 0:
        return line
    hours, minutes, seconds, speed = match.groups()
    current = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    speed_val = float(speed)
    pct = min(100.0, current / total_duration * 100)
    eta = (total_duration - current) / speed_val if speed_val > 0 else None
    eta_str = _format_hms(eta) if eta is not None else "?"
    return f"[{pct:5.1f}% eta {eta_str}] {line}"


def stream_process(
    cmd: list[str],
    log_path: Path,
    total_duration: float | None,
    on_progress: Callable[[str], None] | None = None,
    min_progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
) -> tuple[int, str]:
    """Run `cmd`, writing every output line to `log_path` in real time
    (so `tail -f log_path` gives full-fidelity live monitoring regardless of
    how this function itself is invoked) while forwarding a throttled subset
    to `on_progress` -- every non-tick line (warnings, errors, the encoder's
    startup banner) immediately, periodic progress ticks at most once every
    `min_progress_interval` seconds so a caller capturing stdout wholesale
    (e.g. an agent running this via a shell tool) doesn't get flooded with
    thousands of near-identical lines over a multi-hour encode.

    Works for both ffmpeg and nvencc: only lines starting with `frame=` are
    treated as throttled progress ticks (ffmpeg-style); nvencc's different
    progress format is forwarded promptly as non-tick lines.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tail: list[str] = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert proc.stdout is not None
    last_emit = 0.0
    with open(log_path, "w") as log_file:
        for line in _iter_ffmpeg_lines(proc.stdout):
            log_file.write(line + "\n")
            log_file.flush()
            tail.append(line)
            if len(tail) > 200:
                tail.pop(0)
            if on_progress is None:
                continue
            is_tick = line.startswith(_TICK_PREFIX)
            now = time.monotonic()
            if not is_tick or now - last_emit >= min_progress_interval:
                on_progress(_annotate_progress(line, total_duration))
                last_emit = now
    returncode = proc.wait()
    return returncode, "\n".join(tail)


# Back-compat alias for any external callers/tests.
stream_ffmpeg = stream_process


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


def _attach_cover_remux(video_path: Path, cover_image_path: Path) -> None:
    """NVEncC does not take our Matroska -attach path; remux with ffmpeg to add cover."""
    tmp = video_path.with_name(video_path.stem + ".cover-tmp.mkv")
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"cover-art remux failed ({proc.returncode}): {(proc.stderr or '')[-500:]}"
        )
    tmp.replace(video_path)


def _refresh_track_statistics(video_path: Path) -> None:
    """Measure the encoded tracks and write accurate Matroska BPS/duration/
    frame/byte counters without remuxing the media payload."""
    cmd = ["mkvpropedit", str(video_path), "--add-track-statistics-tags"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"track-statistics refresh failed ({proc.returncode}): {detail}")


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
    on_progress: Callable[[str], None] | None = None,
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
    try:
        probed = probe_file(abs_path)
    except Exception as exc:
        return TranscodeResult(str(rel), "error", f"probe failed: {exc}"), None

    video = probed.get("video")
    if video is None:
        return TranscodeResult(str(rel), "error", "no video stream found"), probed

    nvencc_ok = av1_backend.nvencc_available()
    grain: GrainMeasurement | None = None
    if grain_routing and av1_backend.grain_routing_applies(
        backend_pref, video, gpu_index, nvencc_ok
    ):
        grain = measure_grain(abs_path, probed["format"].get("duration"))
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
        return TranscodeResult(str(rel), "error", str(exc)), probed

    hdr = colorinfo.is_hdr(video)
    preset = presets.select_preset(video["height"], profile, hdr)
    svt_implementation = detect_svt_implementation() if backend == "cpu" else None
    final_path = (
        (output_dir / abs_path.name).with_suffix(".mkv")
        if output_dir
        else abs_path.with_suffix(".mkv")
    )

    if output_dir is not None and final_path.exists() and not overwrite_existing:
        return (
            TranscodeResult(
                str(rel),
                "error",
                f"destination already exists: {final_path} "
                "(pass --overwrite-existing to replace it)",
            ),
            probed,
        )

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
    tmp_path = tmp_dir / f".{abs_path.stem}.av1transcode-tmp.mkv"
    if tmp_path.exists():
        tmp_path.unlink()

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
        returncode, tail = stream_process(
            cmd, log_path, probed["format"].get("duration"), on_progress=on_progress
        )
        if returncode != 0:
            tmp_path.unlink(missing_ok=True)
            tool = "nvencc" if engine == "nvencc" else "ffmpeg"
            return (
                TranscodeResult(str(rel), "error", f"{tool} failed ({returncode}): {tail[-1000:]}"),
                probed,
            )

        if engine == "nvencc" and resolved_cover is not None:
            _attach_cover_remux(tmp_path, resolved_cover)

        _refresh_track_statistics(tmp_path)
        ok, detail = verify_output(probed, tmp_path)
        if not ok:
            tmp_path.unlink(missing_ok=True)
            return TranscodeResult(str(rel), "error", f"verification failed: {detail}"), probed

        if output_dir is not None:
            # Re-checked here (in addition to the early check above) in case
            # something created final_path during the encode itself -- a
            # multi-hour window is plenty of time for that race.
            if final_path.exists() and not overwrite_existing:
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
        else:
            if backup_dir is not None:
                backup_path = Path(backup_dir) / rel
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(abs_path), str(backup_path))
            else:
                abs_path.unlink()
            # Checked only *after* the original is moved/deleted: in-place
            # mode's final_path can legitimately equal abs_path itself (a
            # same-extension source), so checking beforehand would always
            # "collide" with the very file about to be replaced.
            if final_path.exists():
                raise RuntimeError(f"{final_path} already exists, refusing to overwrite")

        shutil.move(str(tmp_path), str(final_path))
        label = f"{engine}/{backend}/{preset.name}"
        if svt_implementation is not None:
            label += f"/{svt_implementation.label}/crf{presets.svt_crf(preset, svt_implementation)}"
        if grain is not None:
            label += f" (grain={grain.score:.4f})"
        return TranscodeResult(str(rel), "changed", f"{label}: {detail}"), probed
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return TranscodeResult(str(rel), "error", str(exc)), probed
