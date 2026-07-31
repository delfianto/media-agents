"""ffmpeg-backed track selection for non-Matroska containers (mp4, m4v, mov, ...).

Uses `-map`/`-c copy` to remux without re-encoding: video and kept audio/
subtitle streams are copied bit-for-bit, only the track list changes.
"""

from collections.abc import Callable

from psammophis.runtime.process import ProcessSupervisor

from . import track_policy
from .scan import probe_file


def normalize_tracks(probed):
    tracks = []
    for s in probed["streams"]:
        if s["codec_type"] not in ("video", "audio", "subtitle"):
            continue
        tracks.append(track_policy.from_ffprobe_stream(s))
    return tracks


def plan(path, policy: track_policy.Policy):
    probed = probe_file(path)
    tracks = normalize_tracks(probed)
    result = track_policy.plan_streams(tracks, policy)
    result["container"] = "ffmpeg"
    result["path"] = str(path)
    return result


def build_command(path, out_path, plan_result):
    cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(path), "-map_metadata", "0", "-map_chapters", "0"]
    for t in plan_result["keep_video"] + plan_result["keep_audio"] + plan_result["keep_subtitle"]:
        cmd += ["-map", f"0:{t['index']}"]
    cmd += ["-c", "copy"]
    cmd += track_policy.ffmpeg_language_metadata_args(
        {
            "a": plan_result["keep_audio"],
            "s": plan_result["keep_subtitle"],
        }
    )
    suffix = str(out_path).lower()
    if suffix.endswith((".mp4", ".m4v", ".mov")):
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out_path))
    return cmd


def remux(
    path,
    out_path,
    plan_result,
    on_heartbeat: Callable[[], None] | None = None,
):
    cmd = build_command(path, out_path, plan_result)
    result = ProcessSupervisor(cmd, on_heartbeat=on_heartbeat).run()
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg remux failed ({result.returncode}): {result.tail[-2000:]}")
    return cmd, result.tail
