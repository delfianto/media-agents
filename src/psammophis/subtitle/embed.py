"""Embed a downloaded external subtitle file as a real MKV subtitle track.

mkvpropedit (what `mkvedit` wraps) can only edit properties on tracks and
attachments that already exist -- it has no way to add a track's payload.
Adding a genuine new subtitle track needs a real remux via mkvmerge, the
same family of operation as `trackstrip`'s own backends.

Safety shape follows `mkvedit.runner.apply()` rather than `trackstrip`'s
root-relative backup-mirror directory: this skill has no root/library
concept (it operates on whatever `--path` names), so the backup is a plain
sibling file (`<video>.subtitle-embed.bak`), like `mkvedit`'s
`<file>.mkvedit.bak`. Temp file -> verify (video/audio intact, one more
subtitle track than before, duration matches, decodes cleanly at the head)
-> backup -> atomic swap; restore from backup on any post-remux failure.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from psammophis.runtime.filesystem import (
    RecoveryRequired,
    discard_staged_backup,
    install_verified,
    path_exists,
    restore_from_backup,
    stage_backup,
)
from psammophis.runtime.process import ProcessSupervisor
from psammophis.runtime.signals import CancellationRequested

DECODE_SPOT_CHECK_SECONDS = 3


class EmbedError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbedResult:
    video: Path
    status: str  # "embedded" | "error"
    detail: str = ""
    command: tuple[str, ...] = ()
    backup: Path | None = None


def _mkvmerge_tracks(path: Path) -> list[dict]:
    proc = subprocess.run(
        ["mkvmerge", "-J", str(path)], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise EmbedError(f"mkvmerge -J failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout).get("tracks", [])


def _ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise EmbedError(f"ffprobe failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def _decode_spot_check(path: Path, seconds: float = DECODE_SPOT_CHECK_SECONDS) -> tuple[bool, str]:
    """Head-only, no seeking -- see trackstrip.apply's identical rationale:
    a backward seek can report a false-positive timestamp artifact at a
    Matroska cluster boundary even when a linear decode is clean."""
    proc = subprocess.run(
        [
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
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or proc.stderr.strip():
        detail = proc.stderr.strip()[:300] or str(proc.returncode)
        return False, f"decode error near start: {detail}"
    return True, "ok"


def _subtitle_language_already_present(tracks: list[dict], language: str) -> bool:
    """True if a subtitle track already carries this language.

    OpenSubtitles languages are ISO 639-1 (e.g. "en"); Matroska tracks
    usually carry ISO 639-2 (`language`, e.g. "eng") and/or an IETF BCP47
    tag (`language_ietf`, e.g. "en") when explicitly set. Comparing via a
    prefix match on the lowercased codes catches both common shapes without
    a full 639-1<->639-2 table -- not exhaustively correct for every
    language pair, but sufficient to avoid the realistic case (embedding
    the same fetched language twice)."""
    language = language.lower()
    for t in tracks:
        if t.get("type") != "subtitles":
            continue
        props = t.get("properties", {})
        for key in ("language_ietf", "language"):
            code = (props.get(key) or "").lower()
            if code and (code.startswith(language) or language.startswith(code)):
                return True
    return False


def build_command(
    video: Path, srt: Path, out: Path, language: str, default_subtitle_ids: list[int]
) -> list[str]:
    cmd = ["mkvmerge", "-o", str(out)]
    for tid in default_subtitle_ids:
        cmd += ["--default-track-flag", f"{tid}:0"]
    cmd.append(str(video))
    cmd += ["--language", f"0:{language}", "--default-track-flag", "0:1", str(srt)]
    return cmd


def plan_command(video: Path, srt: Path, language: str, *, make_default: bool = True) -> list[str]:
    """Dry-run preview: the exact mkvmerge command, without running it."""
    tracks = _mkvmerge_tracks(video)
    default_subtitle_ids = (
        [
            t["id"]
            for t in tracks
            if t.get("type") == "subtitles" and t.get("properties", {}).get("default_track")
        ]
        if make_default
        else []
    )
    preview_out = video.with_name(video.stem + ".embedded" + video.suffix)
    return build_command(video, srt, preview_out, language, default_subtitle_ids)


def embed(
    video: Path,
    srt: Path,
    language: str,
    *,
    yes: bool,
    make_default: bool = True,
    backup_suffix: str = ".subtitle-embed.bak",
    on_heartbeat: Callable[[], None] | None = None,
) -> EmbedResult:
    if not srt.is_file():
        return EmbedResult(video, "error", f"subtitle file does not exist: {srt}")
    try:
        tracks = _mkvmerge_tracks(video)
    except EmbedError as exc:
        return EmbedResult(video, "error", str(exc))
    if _subtitle_language_already_present(tracks, language):
        return EmbedResult(video, "skipped", f"a {language!r} subtitle track already exists")
    original_subtitle_count = sum(1 for t in tracks if t.get("type") == "subtitles")
    default_subtitle_ids = (
        [
            t["id"]
            for t in tracks
            if t.get("type") == "subtitles" and t.get("properties", {}).get("default_track")
        ]
        if make_default
        else []
    )

    tmp_path = video.with_name(f".{video.stem}.subtitle-embed-tmp{video.suffix}")
    if path_exists(tmp_path):
        return EmbedResult(
            video,
            "error",
            f"temporary work file already exists: {tmp_path}; inspect or remove it before retrying",
        )
    command = build_command(video, srt, tmp_path, language, default_subtitle_ids)
    if not yes:
        return EmbedResult(video, "planned", "dry run", tuple(command))

    backup = video.with_name(video.name + backup_suffix)
    if path_exists(backup):
        return EmbedResult(video, "error", f"backup already exists: {backup}", tuple(command))

    backup_staged = False
    try:
        proc = ProcessSupervisor(command, on_heartbeat=on_heartbeat).run()
        if proc.returncode >= 2:  # mkvmerge: 0 ok, 1 ok-with-warnings, 2+ error
            tmp_path.unlink(missing_ok=True)
            return EmbedResult(
                video, "error", f"mkvmerge embed failed ({proc.returncode}): {proc.tail[-2000:]}"
            )

        original = _ffprobe(video)
        merged = _ffprobe(tmp_path)
        v = sum(1 for s in merged.get("streams", []) if s.get("codec_type") == "video")
        a = sum(1 for s in merged.get("streams", []) if s.get("codec_type") == "audio")
        s = sum(1 for s in merged.get("streams", []) if s.get("codec_type") == "subtitle")
        if v < 1 or a < 1:
            tmp_path.unlink(missing_ok=True)
            return EmbedResult(video, "error", "output is missing a video or audio stream")
        if s != original_subtitle_count + 1:
            tmp_path.unlink(missing_ok=True)
            return EmbedResult(
                video,
                "error",
                f"expected {original_subtitle_count + 1} subtitle tracks in output, found {s}",
            )
        orig_dur = original.get("format", {}).get("duration")
        new_dur = merged.get("format", {}).get("duration")
        if orig_dur and new_dur:
            tolerance = max(2.0, float(orig_dur) * 0.02)
            if abs(float(orig_dur) - float(new_dur)) > tolerance:
                tmp_path.unlink(missing_ok=True)
                return EmbedResult(video, "error", f"duration mismatch: {orig_dur}s -> {new_dur}s")
        ok, detail = _decode_spot_check(tmp_path)
        if not ok:
            tmp_path.unlink(missing_ok=True)
            return EmbedResult(video, "error", f"verification failed: {detail}")

        stage_backup(video, backup, allow_hardlink=False)
        backup_staged = True
        install_verified(video, tmp_path, video)
        return EmbedResult(video, "embedded", "ok", tuple(command), backup)
    except KeyboardInterrupt, SystemExit, CancellationRequested:
        if backup_staged and path_exists(backup) and path_exists(video):
            discard_staged_backup(backup)
        tmp_path.unlink(missing_ok=True)
        raise
    except RecoveryRequired:
        raise
    except Exception as exc:
        if backup_staged:
            try:
                restore_from_backup(backup, video)
            except Exception as restore_exc:
                return EmbedResult(
                    video,
                    "error",
                    f"embed failed: {exc}; restore also failed: {restore_exc}",
                    tuple(command),
                    backup,
                )
            return EmbedResult(
                video,
                "error",
                f"embed failed and original was restored: {exc}",
                tuple(command),
                backup,
            )
        tmp_path.unlink(missing_ok=True)
        return EmbedResult(video, "error", str(exc), tuple(command))
