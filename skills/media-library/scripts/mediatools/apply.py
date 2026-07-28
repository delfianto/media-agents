"""Per-file remux orchestration: plan, verify, back up, and swap in place.

Safety model:
  - Every apply() call re-derives the keep/drop plan from a fresh,
    container-native probe (mkvmerge -J for .mkv, ffprobe for everything
    else) rather than trusting the scan cache, which may be stale.
  - Remuxing always writes to a temp file next to the original first.
  - The temp file is verified (has video+audio, duration matches, and
    decodes cleanly at both ends) before anything happens to the original.
  - The original is moved into a backup directory (mirroring the library's
    relative path layout) rather than deleted, unless the caller passes
    backup_dir=None explicitly.
"""

import shutil
import subprocess
from pathlib import Path

from . import remux_ffmpeg, remux_mkv, track_policy
from .scan import probe_file, walk_media_files

DECODE_SPOT_CHECK_SECONDS = 3


def get_backend(path):
    return remux_mkv if str(path).lower().endswith(".mkv") else remux_ffmpeg


def iter_target_files(root, path_filter=None, limit=None):
    count = 0
    for p in walk_media_files(root):
        rel = str(p.relative_to(root))
        if path_filter and path_filter.lower() not in rel.lower():
            continue
        yield p
        count += 1
        if limit and count >= limit:
            return


def candidates_from_cache(cache, policy, path_filter=None):
    """Fast, approximate pass over the scan cache for reporting/planning.
    Does not touch disk. apply_one() re-verifies with a live probe."""
    for rel, entry in sorted(cache.get("files", {}).items()):
        if "error" in entry:
            continue
        if path_filter and path_filter.lower() not in rel.lower():
            continue
        tracks = [
            track_policy.from_ffprobe_stream(s)
            for s in entry["streams"]
            if s["codec_type"] in ("video", "audio", "subtitle")
        ]
        result = track_policy.plan_streams(tracks, policy)
        yield rel, entry, result


# ffmpeg -v error messages that are known false positives, not evidence of a
# broken file -- confirmed independently twice: an AV1 remux (Arcane S01E01,
# tail-seek check -- see the head-only design note below) and a plain HEVC
# original (His Dark Materials S01, transcode input -- same message appears
# on the *untouched original* with zero seeking involved, so it cannot be
# something a remux/transcode introduces). Both cases fully decode clean
# start-to-finish despite the warning; it's evidently a source-encoder
# timestamp quirk (e.g. duplicate DTS on an early B-frame) that some players'
# demuxers tolerate silently but ffmpeg's null-muxer flags at -v error level.
# Genuine corruption looks nothing like this (see Silo S01E07: "Invalid NAL
# unit size", "Packet corrupt", "Decode error rate exceeds maximum") so
# filtering this one specific, well-evidenced message is safe -- it is not a
# blanket "ignore all warnings" escape hatch.
_BENIGN_STDERR_SUBSTRINGS = ("non monotonically increasing dts",)


def _filter_benign_stderr(stderr_text):
    lines = [line for line in stderr_text.splitlines() if line.strip()]
    return [line for line in lines if not any(p in line.lower() for p in _BENIGN_STDERR_SUBSTRINGS)]


def _decode_spot_check(path, seconds=DECODE_SPOT_CHECK_SECONDS):
    """Actually decode the first few seconds of the remuxed file. The
    metadata checks below catch a truncated/malformed container, but only a
    real decode catches a corrupt stream inside an otherwise well-formed one.

    Deliberately head-only, no seeking: a backward seek (-sseof/-ss) into
    some AV1 remuxes reliably trips ffmpeg's null-muxer DTS-monotonicity
    check on the first few frames after the seek, even though mkvmerge
    exited 0 and a full linear decode of the same file is completely clean.
    That's mkvmerge writing different-but-valid cue/cluster boundaries than
    the source encoder interacting badly with AV1's reference structure on
    cold seek -- a seek artifact, not corruption -- so a tail check done this
    way would flag good remuxes as broken.
    """
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
    real_errors = _filter_benign_stderr(proc.stderr)
    if proc.returncode != 0 or real_errors:
        detail = "\n".join(real_errors)[:300] or proc.stderr.strip()[:300] or str(proc.returncode)
        return False, f"decode error near start: {detail}"
    return True, "ok"


def verify_output(original_probed, new_path):
    new_probed = probe_file(new_path)
    v = sum(1 for s in new_probed["streams"] if s["codec_type"] == "video")
    a = sum(1 for s in new_probed["streams"] if s["codec_type"] == "audio")
    if v < 1:
        return False, "output has no video stream"
    if a < 1:
        return False, "output has no audio stream"
    new_size = new_probed["format"].get("size") or 0
    if new_size <= 0:
        return False, "output file is empty"
    orig_dur = original_probed["format"].get("duration")
    new_dur = new_probed["format"].get("duration")
    if orig_dur and new_dur:
        tolerance = max(2.0, orig_dur * 0.02)
        if abs(orig_dur - new_dur) > tolerance:
            return False, f"duration mismatch: {orig_dur:.1f}s -> {new_dur:.1f}s"

    ok, detail = _decode_spot_check(new_path)
    if not ok:
        return False, detail
    return True, "ok"


class ApplyResult:
    def __init__(self, rel, status, detail=""):
        self.rel = rel
        self.status = status  # "changed" | "unchanged" | "planned" | "error"
        self.detail = detail


def _execute_backend_plan(
    abs_path: Path, root: Path, backend, plan_result, backup_dir, execute: bool, preview_suffix: str
):
    """Shared plan-execution machinery for any backend exposing
    build_command(path, out_path, plan_result) and remux(path, out_path, plan_result):
    temp file -> verify -> backup-or-delete original -> swap in. Used by both
    the strip backends (remux_mkv/remux_ffmpeg) and transcode.py."""
    rel = abs_path.relative_to(root)

    if not plan_result["changed"]:
        return ApplyResult(str(rel), "unchanged"), plan_result

    if not execute:
        preview_out = abs_path.with_name(abs_path.stem + preview_suffix + abs_path.suffix)
        cmd = backend.build_command(abs_path, preview_out, plan_result)
        return ApplyResult(str(rel), "planned", " ".join(str(c) for c in cmd)), plan_result

    tmp_path = abs_path.with_name(f".{abs_path.stem}.mediatools-tmp{abs_path.suffix}")
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        original_probed = probe_file(abs_path)
        backend.remux(abs_path, tmp_path, plan_result)
        ok, detail = verify_output(original_probed, tmp_path)
        if not ok:
            tmp_path.unlink(missing_ok=True)
            return ApplyResult(str(rel), "error", f"verification failed: {detail}"), plan_result

        if backup_dir is not None:
            backup_path = Path(backup_dir) / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(abs_path), str(backup_path))
        else:
            abs_path.unlink()

        shutil.move(str(tmp_path), str(abs_path))
        return ApplyResult(str(rel), "changed", detail), plan_result
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        return ApplyResult(str(rel), "error", str(exc)), plan_result


def apply_one(abs_path: Path, root: Path, policy: track_policy.Policy, backup_dir, execute: bool):
    backend = get_backend(abs_path)
    try:
        plan_result = backend.plan(abs_path, policy)
    except Exception as exc:
        return ApplyResult(str(abs_path.relative_to(root)), "error", f"probe failed: {exc}"), None
    return _execute_backend_plan(
        abs_path, root, backend, plan_result, backup_dir, execute, ".stripped"
    )


def transcode_one(
    abs_path: Path, root: Path, from_codecs, to_codec, bitrate, backup_dir, execute: bool
):
    from . import transcode as transcode_mod

    try:
        plan_result = transcode_mod.plan(abs_path, from_codecs, to_codec, bitrate)
    except Exception as exc:
        return ApplyResult(str(abs_path.relative_to(root)), "error", f"probe failed: {exc}"), None
    return _execute_backend_plan(
        abs_path, root, transcode_mod, plan_result, backup_dir, execute, ".transcoded"
    )
