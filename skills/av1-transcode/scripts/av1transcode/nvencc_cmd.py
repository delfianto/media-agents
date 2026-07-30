"""Build argv for rigaya's NVEncC when dynamic HDR metadata must be kept.

ffmpeg's `av1_nvenc` has no Dolby Vision RPU or HDR10+ dynamic passthrough.
NVEncC (package name `nvenc` on this machine, binary `nvencc`) is built with
libdovi and exposes:

  --dolby-vision-rpu copy|file
  --dolby-vision-profile 10.0|10.1|...
  --dhdr10-info copy|file

`dovi_tool` / `hdr10plus_tool` only inject into *HEVC* bitstreams on the
versions installed here, so they are not used for AV1 inject -- only optional
extract fallbacks outside this module.

This module is pure argv building + PATH detection (no encode I/O).
"""

from __future__ import annotations

from pathlib import Path

from medialib import av1_presets as presets
from medialib import colorinfo

from . import langfilter

# AV1 Dolby Vision profile: 10.1 is the common backward-compatible single-layer
# choice for OTT/player compatibility (see reference/presets.md). Smoke-test
# before treating as universal -- wrong profile can mean no DV light on device.
DEFAULT_AV1_DV_PROFILE = "10.1"

# Our ffmpeg presets use NVENC p1-p7 names; NVEncC's -u only accepts
# default|performance|quality. Map the high-quality end of our table to
# "quality" (we always use p7 after the p6-vs-p7 incident).
_NVENC_PRESET_TO_NVENCC = {
    "p7": "quality",
    "p6": "quality",
    "p5": "default",
    "p4": "default",
    "p3": "performance",
    "p2": "performance",
    "p1": "performance",
}


def stream_to_nvencc_track_number(streams: list[dict], stream: dict) -> int:
    """1-based track number among streams of the same type (audio or sub),
    which is what NVEncC's --audio-copy N / --sub-copy N expect -- not the
    absolute ffprobe stream index."""
    target = stream["index"]
    for i, s in enumerate(streams, start=1):
        if s["index"] == target:
            return i
    raise ValueError(f"stream index {target} not found in stream list")


def map_nvenc_preset(nvenc_preset: str) -> str:
    return _NVENC_PRESET_TO_NVENCC.get(nvenc_preset, "quality")


def build_nvencc_command(
    input_path: str | Path,
    output_path: str | Path,
    probed: dict,
    preset: presets.Preset,
    gpu_index: int = 0,
    drop_subtitles: bool = False,
    audio_lang: str = langfilter.ALL,
    subtitle_lang: str = langfilter.ALL,
    single_audio_track: bool = True,
    max_bitrate_fraction: float | None = presets.MAX_BITRATE_FRACTION_OF_SOURCE,
    dolby_vision_rpu: str = "copy",
    dolby_vision_profile: str = DEFAULT_AV1_DV_PROFILE,
    dhdr10_info: str = "copy",
) -> list[str]:
    """Build a full NVEncC argv that encodes AV1 + Opus and preserves DV/HDR10+.

    `dolby_vision_rpu` / `dhdr10_info` are either the literal \"copy\" or a
    path to an extracted RPU/JSON file. Flags are only added when the source
    actually has that metadata type.
    """
    video = probed.get("video")
    if video is None:
        raise ValueError(f"{input_path}: no video stream found")

    all_audio = probed.get("audio", [])
    all_subs = probed.get("subtitles", [])
    kept_audio, _fallback = langfilter.filter_audio(
        all_audio, audio_lang, single=single_audio_track
    )
    kept_subs = [] if drop_subtitles else langfilter.filter_subtitles(all_subs, subtitle_lang)
    if not kept_audio:
        raise ValueError(f"{input_path}: no audio tracks left after language filter")

    cap_bps = (
        presets.max_bitrate_bps(probed, max_bitrate_fraction)
        if max_bitrate_fraction is not None
        else None
    )

    cmd: list[str] = [
        "nvencc",
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "-c",
        "av1",
        "--output-depth",
        "10",
        "--device",
        str(gpu_index),
        "-u",
        map_nvenc_preset(preset.nvenc_preset),
        # QVBR: quality-targeted VBR; maps our ffmpeg -cq intent.
        "--qvbr",
        str(preset.nvenc_cq),
        "--multipass",
        "2pass-full",
        "--avsw",  # software decode of source (DV RPU readable; no CUDA decode quirks)
    ]

    if cap_bps:
        # NVEncC wants kbps.
        cmd += ["--max-bitrate", str(max(1, cap_bps // 1000))]

    if colorinfo.has_dolby_vision(video):
        cmd += [
            "--dolby-vision-rpu",
            dolby_vision_rpu,
            "--dolby-vision-profile",
            dolby_vision_profile,
        ]

    if colorinfo.has_hdr10_plus(video):
        cmd += ["--dhdr10-info", dhdr10_info]

    # Explicit color/HDR flags (colorprim/transfer/colormatrix/colorrange/
    # master-display/max-cll), not "--video-metadata copy" -- that flag only
    # copies freeform per-stream tags, not bitstream color signaling, and as
    # a side effect drags forward stale mkvmerge statistics (BPS,
    # NUMBER_OF_BYTES) from the source's own track. See colorinfo.nvencc_hdr_args.
    cmd += colorinfo.nvencc_hdr_args(video)
    cmd += ["--video-metadata", "clear"]

    audio_tracks = [stream_to_nvencc_track_number(all_audio, s) for s in kept_audio]
    # Encode only the kept tracks: copy-select then re-encode those to Opus.
    # NVEncC: --audio-copy N,M selects tracks; --audio-codec libopus encodes them.
    copy_list = ",".join(str(n) for n in audio_tracks)
    cmd += ["--audio-copy", copy_list]
    for s, track_n in zip(kept_audio, audio_tracks, strict=True):
        br = presets.opus_bitrate_kbps(s["channels"])
        cmd += ["--audio-codec", f"{track_n}?libopus"]
        cmd += ["--audio-bitrate", f"{track_n}?{br}"]

    if kept_subs:
        sub_tracks = [stream_to_nvencc_track_number(all_subs, s) for s in kept_subs]
        cmd += ["--sub-copy", ",".join(str(n) for n in sub_tracks)]

    cmd += ["--chapter-copy"]
    return cmd
