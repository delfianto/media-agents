"""Build the ffmpeg command line for one file: `-map 0` (video, audio,
subtitles, and font/attachment streams all carried through), video re-encoded
to AV1 (libsvtav1 or av1_nvenc per `backend`), every audio track re-encoded to
Opus at a bitrate scaled to its channel count, subtitles/attachments/chapters/
metadata stream-copied untouched.
"""

from pathlib import Path

from . import colorinfo, presets

# scd=1 (scene change detection): keyframes land on actual cuts rather than
# only the fixed GOP interval, a plain efficiency win with no quality
# downside -- recommended as a baseline in community SVT-AV1 guides
# (ffmpeg.party) and cheap enough to apply unconditionally rather than
# thread through every preset entry.
_BASE_SVT_PARAMS = {"scd": "1"}


def build_command(
    input_path: str | Path,
    output_path: str | Path,
    probed: dict,
    preset: presets.Preset,
    backend: str,
    gpu_index: int | None = None,
    drop_subtitles: bool = False,
) -> list[str]:
    video = probed.get("video")
    if video is None:
        raise ValueError(f"{input_path}: no video stream found")

    hdr = colorinfo.is_hdr(video)
    dolby_vision = colorinfo.has_dolby_vision(video)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-i",
        str(input_path),
        "-map",
        "0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
    ]

    if backend == "cpu":
        cmd += _svtav1_video_args(video, preset, hdr, dolby_vision)
    elif backend == "nvenc":
        if dolby_vision:
            raise ValueError(
                f"{input_path}: source carries Dolby Vision RPU metadata, which "
                "av1_nvenc cannot preserve -- use backend='cpu' (libsvtav1 supports it)"
            )
        if gpu_index is None:
            raise ValueError("nvenc backend requested but no AV1-capable GPU is available")
        cmd += _nvenc_video_args(preset, gpu_index)
    else:
        raise ValueError(f"unknown backend {backend!r}, expected 'cpu' or 'nvenc'")

    # Generic output-level color tags, independent of encoder: what makes the
    # container (and, for encoders that read AVCodecContext color fields into
    # their bitstream headers, the AV1 sequence header itself) advertise the
    # right primaries/transfer/matrix regardless of which backend encoded it.
    if video.get("color_primaries"):
        cmd += ["-color_primaries", video["color_primaries"]]
    if video.get("color_transfer"):
        cmd += ["-color_trc", video["color_transfer"]]
    if video.get("color_space"):
        cmd += ["-colorspace", video["color_space"]]

    cmd += _audio_args(probed.get("audio", []))

    if drop_subtitles:
        cmd += ["-sn"]
    else:
        cmd += ["-c:s", "copy", "-c:t", "copy"]

    cmd.append(str(output_path))
    return cmd


def _svtav1_video_args(
    video: dict, preset: presets.Preset, hdr: bool, dolby_vision: bool
) -> list[str]:
    params = dict(_BASE_SVT_PARAMS)
    params.update(preset.svt_extra)
    params["tune"] = str(preset.svt_tune)
    if preset.film_grain:
        params["film-grain"] = str(preset.film_grain)
    if hdr:
        params.update(colorinfo.svtav1_hdr_params(video))

    args = [
        "-c:v",
        "libsvtav1",
        "-preset",
        str(preset.svt_preset),
        "-crf",
        str(preset.crf),
        "-pix_fmt",
        "yuv420p10le",
    ]
    if dolby_vision:
        # SvtAv1EncApp's own default ("auto") already detects and re-injects
        # RPU side data when present, but it's set explicitly here so a dry
        # run's printed command shows *why* this file gets DV handling
        # rather than relying on a silent default.
        args += ["-dolbyvision", "1"]
    args += ["-svtav1-params", ":".join(f"{k}={v}" for k, v in params.items())]
    return args


def _nvenc_video_args(preset: presets.Preset, gpu_index: int) -> list[str]:
    args = [
        "-c:v",
        "av1_nvenc",
        "-gpu",
        str(gpu_index),
        "-preset",
        preset.nvenc_preset,
        "-tune",
        preset.nvenc_tune,
        "-rc",
        "vbr",
        "-cq",
        str(preset.nvenc_cq),
        "-b:v",
        "0",
        "-multipass",
        "fullres",
        "-rc-lookahead",
        "32",
        # "each" is a documented -b_ref_mode value and ffmpeg accepts it on
        # the command line, but the driver rejects it at encoder-open time
        # for av1_nvenc specifically ("Each B frame as reference is not
        # supported" -> "No capable devices found") -- confirmed directly
        # against this machine's RTX 4080. "middle" (every other B-frame as
        # a reference) is the strongest mode that actually opens.
        "-b_ref_mode",
        "middle",
        "-pix_fmt",
        "p010le",
    ]
    for key, value in preset.nvenc_extra.items():
        args += [f"-{key}", value]
    return args


def _audio_args(audio_streams: list[dict]) -> list[str]:
    args = []
    for i, stream in enumerate(audio_streams):
        bitrate = presets.opus_bitrate_kbps(stream["channels"])
        args += [f"-c:a:{i}", "libopus", f"-b:a:{i}", f"{bitrate}k"]
    return args
