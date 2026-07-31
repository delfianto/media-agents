"""mkvmerge-backed track selection for .mkv files.

mkvmerge is the native Matroska muxer: it preserves chapters, attachments
(e.g. embedded subtitle fonts) and tags by default, and its --audio-tracks /
--subtitle-tracks options are an exact "keep only these track IDs" filter, so
no re-encoding and no manual stream-copy mapping is needed.
"""

import json
import subprocess
from collections.abc import Callable

from psammophis.runtime.process import ProcessSupervisor

from . import track_policy


def identify(path):
    proc = subprocess.run(
        ["mkvmerge", "-J", str(path)], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mkvmerge -J failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout)


def normalize_tracks(identify_json):
    tracks = []
    for t in identify_json.get("tracks", []):
        norm = track_policy.from_mkvmerge_track(t)
        if norm is not None:
            tracks.append(norm)
    return tracks


def plan(path, policy: track_policy.Policy):
    info = identify(path)
    tracks = normalize_tracks(info)
    result = track_policy.plan_streams(tracks, policy)
    result["container"] = "mkv"
    result["path"] = str(path)
    return result


def build_command(path, out_path, plan_result):
    cmd = ["mkvmerge", "-o", str(out_path)]
    for t in plan_result["keep_audio"] + plan_result["keep_subtitle"]:
        lang = t.get("resolved_lang")
        if lang:
            cmd += ["--language", f"{t['index']}:{lang}"]
    if plan_result["drop_audio"]:
        keep_ids = ",".join(str(t["index"]) for t in plan_result["keep_audio"])
        cmd += ["--audio-tracks", keep_ids] if keep_ids else ["--no-audio"]
    if plan_result["drop_subtitle"]:
        keep_ids = ",".join(str(t["index"]) for t in plan_result["keep_subtitle"])
        cmd += ["--subtitle-tracks", keep_ids] if keep_ids else ["--no-subtitles"]
    cmd.append(str(path))
    return cmd


def remux(
    path,
    out_path,
    plan_result,
    on_heartbeat: Callable[[], None] | None = None,
):
    cmd = build_command(path, out_path, plan_result)
    result = ProcessSupervisor(cmd, on_heartbeat=on_heartbeat).run()
    # mkvmerge exit codes: 0 = ok, 1 = ok with warnings, 2 = error (aborted).
    if result.returncode >= 2:
        raise RuntimeError(f"mkvmerge remux failed ({result.returncode}): {result.tail[-2000:]}")
    return cmd, result.tail
