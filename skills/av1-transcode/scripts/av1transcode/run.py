"""Per-file execution: pick a backend, run ffmpeg with a live-streamed and
persisted log (an AV1 encode runs minutes to hours, not the seconds a
media-library stream-copy takes, so silently buffering output until exit
like transcode.py does isn't acceptable here), verify the result, and swap it
in behind a backup. Same temp-file -> verify -> backup -> swap shape as
media-library's apply.py, just built for a much longer-running job.
"""

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from . import colorinfo, langfilter, nvencc_cmd, presets
from . import command as command_mod
from .probe import probe_file

DECODE_SPOT_CHECK_SECONDS = 3
DEFAULT_PROGRESS_INTERVAL = 10.0  # seconds between throttled progress lines to stdout

# Conventional poster/cover filenames -- checked in this order next to the
# source video. "poster.jpg" is what media-organizer's `run` itself leaves
# behind once a movie is identified (see
# skills/media-organizer/reference/naming-conventions.md); the rest are the
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


def choose_backend(
    video: dict,
    requested: str,
    gpu_index: int | None,
    nvencc_ok: bool | None = None,
) -> str:
    """Resolve 'auto' into 'cpu' or 'nvenc'.

    Dolby Vision / HDR10+ cannot be preserved by plain ffmpeg `av1_nvenc`.
    When `nvencc` is available those sources stay on the GPU path (NVEncC
    attaches RPU / HDR10+). Without nvencc, DV falls back to CPU
    (libsvtav1 `-dolbyvision`); forcing `--backend nvenc` on DV without
    nvencc is an error rather than a silent metadata drop.
    """
    if nvencc_ok is None:
        nvencc_ok = nvencc_cmd.nvencc_available()
    needs_dyn = colorinfo.needs_dynamic_metadata_path(video)

    if requested == "cpu":
        return "cpu"
    if requested == "nvenc":
        if gpu_index is None:
            raise ValueError("nvenc backend requested but no AV1-capable GPU is available")
        if colorinfo.has_dolby_vision(video) and not nvencc_ok:
            raise ValueError(
                "source has Dolby Vision metadata; plain ffmpeg av1_nvenc would drop it "
                "and nvencc was not found on PATH -- install nvencc (rigaya NVEnc) or "
                "pass --backend cpu (libsvtav1 preserves RPU via -dolbyvision)"
            )
        return "nvenc"
    # auto
    if gpu_index is None:
        return "cpu"
    if needs_dyn and colorinfo.has_dolby_vision(video) and not nvencc_ok:
        return "cpu"
    return "nvenc"


def choose_encode_engine(backend: str, video: dict, nvencc_ok: bool | None = None) -> str:
    """Return 'ffmpeg' or 'nvencc' for the concrete encode tool.

    Dynamic metadata on a GPU backend requires nvencc; everything else uses
    the existing ffmpeg builders (libsvtav1 or av1_nvenc).
    """
    if nvencc_ok is None:
        nvencc_ok = nvencc_cmd.nvencc_available()
    if backend == "nvenc" and colorinfo.needs_dynamic_metadata_path(video):
        if not nvencc_ok:
            # HDR10+ without nvencc: fall through to ffmpeg (static HDR only).
            # DV without nvencc should never reach here for explicit nvenc
            # (choose_backend errors); auto already rewrote to cpu.
            return "ffmpeg"
        return "nvencc"
    return "ffmpeg"


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
    # Dolby Vision first: nvencc's DV path often leaves container color_transfer /
    # mastering-display tags empty even when RPU + DOVI config are present
    # (confirmed on Dune Part One remux clip: PQ tags gone, rpu_present_flag=1,
    # dv_profile=10). DV with RPU is sufficient proof of HDR retention.
    if colorinfo.has_dolby_vision(orig_video) and not colorinfo.has_dolby_vision(new_video):
        return False, "source had Dolby Vision but output is missing DOVI configuration record"
    if colorinfo.is_hdr(orig_video) and not colorinfo.has_dolby_vision(new_video):
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
    if orig_size and new_size and new_size > orig_size:
        return True, f"ok (warning: output {new_size}B > source {orig_size}B -- not smaller!)"
    return True, "ok"


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

    nvencc_ok = nvencc_cmd.nvencc_available()
    try:
        backend = choose_backend(video, backend_pref, gpu_index, nvencc_ok=nvencc_ok)
        engine = choose_encode_engine(backend, video, nvencc_ok=nvencc_ok)
    except ValueError as exc:
        return TranscodeResult(str(rel), "error", str(exc)), probed

    hdr = colorinfo.is_hdr(video)
    preset = presets.select_preset(video["height"], profile, hdr)
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
        )
        detail = " ".join(str(c) for c in cmd)
        if engine == "nvencc" and resolved_cover is not None:
            detail += f" && ffmpeg-cover-attach {resolved_cover}"
        return TranscodeResult(str(rel), "planned", detail), probed

    tmp_path = abs_path.with_name(f".{abs_path.stem}.av1transcode-tmp.mkv")
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
        return TranscodeResult(str(rel), "changed", f"{label}: {detail}"), probed
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return TranscodeResult(str(rel), "error", str(exc)), probed
