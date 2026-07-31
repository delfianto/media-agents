"""Audio codec transcode: re-encode matching audio streams to a target codec
while video (and every other audio/subtitle stream) is stream-copied as-is.

Unlike remux_mkv.py/remux_ffmpeg.py -- which only ever choose which existing
streams to keep verbatim -- this module actually re-encodes audio. That's
needed for codecs that pass through cleanly on some receivers but not others
(e.g. DTS/DTS-HD MA muted on some LG TVs over eARC): dropping the track
outright would leave a file with no audio at all, so the fix is to replace it
with something universally compatible instead.

Always uses ffmpeg regardless of container, since mkvmerge cannot transcode.
"""

import subprocess

from . import track_policy
from .scan import probe_file


def plan(path, from_codecs, to_codec="eac3", bitrate="640k"):
    probed = probe_file(path)
    streams = probed["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    matching = [s for s in audio if s.get("codec_name") in from_codecs]
    return {
        "path": str(path),
        "streams": streams,
        "matching": matching,
        "to_codec": to_codec,
        "bitrate": bitrate,
        "changed": bool(matching),
    }


def build_command(path, out_path, plan_result):
    streams = plan_result["streams"]
    matching_indices = {s["index"] for s in plan_result["matching"]}
    to_codec = plan_result["to_codec"]
    bitrate = plan_result["bitrate"]

    kept = [s for s in streams if s["codec_type"] in ("video", "audio", "subtitle")]
    cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(path), "-map_metadata", "0", "-map_chapters", "0"]
    for s in kept:
        cmd += ["-map", f"0:{s['index']}"]

    cmd += ["-c:v", "copy", "-c:s", "copy"]
    audio_streams = [s for s in kept if s["codec_type"] == "audio"]
    subtitle_streams = [s for s in kept if s["codec_type"] == "subtitle"]
    for i, s in enumerate(audio_streams):
        if s["index"] in matching_indices:
            cmd += [f"-c:a:{i}", to_codec, f"-b:a:{i}", bitrate]
        else:
            cmd += [f"-c:a:{i}", "copy"]

    # transcode.py doesn't run the language-filtering policy (it only
    # touches codec, not language), so there's no anime-aware keep-set to
    # resolve against here -- English is the only sensible default for a
    # library that's already been through the language cleanup by the time
    # transcode normally runs.
    cmd += track_policy.ffmpeg_language_metadata_args(
        {
            "a": [
                {
                    "resolved_lang": track_policy.resolve_language(
                        s.get("language"), track_policy.ENGLISH_ONLY
                    )
                }
                for s in audio_streams
            ],
            "s": [
                {
                    "resolved_lang": track_policy.resolve_language(
                        s.get("language"), track_policy.ENGLISH_ONLY
                    )
                }
                for s in subtitle_streams
            ],
        }
    )

    suffix = str(out_path).lower()
    if suffix.endswith((".mp4", ".m4v", ".mov")):
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out_path))
    return cmd


def remux(path, out_path, plan_result):
    cmd = build_command(path, out_path, plan_result)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg transcode failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}"
        )
    return cmd, proc.stderr
