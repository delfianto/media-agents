"""Build the ffmpeg command line for one file: video re-encoded to AV1
(libsvtav1 or av1_nvenc per `backend`, capped to a fraction of the source's
own bitrate -- see presets.MAX_BITRATE_FRACTION_OF_SOURCE), the single
best-quality audio track matching `audio_lang` re-encoded to Opus (falling
back to every track if none match -- see langfilter.filter_audio), plain
(non-SDH) subtitle tracks matching `subtitle_lang` stream-copied,
fonts/attachments always kept, chapters retained, meaningful stream metadata
re-applied explicitly (without stale source codec statistics),
and an optional cover image added as a proper Matroska attachment (not an
embedded video stream -- see reference/incidents.md for why that
distinction matters).

Streams are mapped explicitly by index (not a blanket `-map 0`) so a source
cover-art "video" stream never gets swept into the AV1 encode alongside the
real one, and so language/quality filtering has something precise to filter.
"""

from pathlib import Path

from medialib import av1_presets as presets
from medialib import colorinfo
from medialib.svt import (
    SvtImplementation,
    detect_svt_implementation,
    implementation_params,
)

from . import langfilter

_IMAGE_MIMETYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def build_command(
    input_path: str | Path,
    output_path: str | Path,
    probed: dict,
    preset: presets.Preset,
    backend: str,
    gpu_index: int | None = None,
    drop_subtitles: bool = False,
    audio_lang: str = langfilter.ALL,
    subtitle_lang: str = langfilter.ALL,
    single_audio_track: bool = True,
    max_bitrate_fraction: float | None = presets.MAX_BITRATE_FRACTION_OF_SOURCE,
    cover_image_path: str | Path | None = None,
    svt_implementation: SvtImplementation | None = None,
) -> list[str]:
    video = probed.get("video")
    if video is None:
        raise ValueError(f"{input_path}: no video stream found")

    hdr = colorinfo.is_hdr(video)
    dolby_vision = colorinfo.has_dolby_vision(video)
    cap_bps = (
        presets.max_bitrate_bps(probed, max_bitrate_fraction)
        if max_bitrate_fraction is not None
        else None
    )

    kept_audio, _audio_fallback = langfilter.filter_audio(
        probed.get("audio", []), audio_lang, single=single_audio_track
    )
    kept_subtitles = (
        []
        if drop_subtitles
        else langfilter.filter_subtitles(probed.get("subtitles", []), subtitle_lang)
    )

    cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(input_path)]
    cmd += ["-map", f"0:{video['index']}"]
    for s in kept_audio:
        cmd += ["-map", f"0:{s['index']}"]
    for s in kept_subtitles:
        cmd += ["-map", f"0:{s['index']}"]
    cmd += ["-map", "0:t?"]  # font/attachment streams, if any -- never filtered
    # Do not inherit the source's global timestamp/encoder fields or its
    # per-video/audio mkvmerge statistics. Those describe the HEVC/TrueHD
    # source and become impossible values after AV1/Opus transcoding (for
    # example NUMBER_OF_BYTES larger than the entire output). Stream-specific
    # dummy mappings disable ffmpeg's automatic metadata copy for only the
    # transcoded stream types; copied subtitles and attachments keep their
    # language/font metadata. Chapters are mapped independently.
    cmd += [
        "-map_metadata",
        "-1",
        "-map_metadata:s:v",
        "-1",
        "-map_metadata:s:a",
        "-1",
        "-map_chapters",
        "0",
    ]

    if backend == "cpu":
        implementation = svt_implementation or detect_svt_implementation()
        cmd += _svtav1_video_args(video, preset, hdr, dolby_vision, cap_bps, implementation)
    elif backend == "nvenc":
        if dolby_vision:
            raise ValueError(
                f"{input_path}: source carries Dolby Vision RPU metadata, which "
                "av1_nvenc cannot preserve -- use backend='cpu' (libsvtav1 supports it)"
            )
        if gpu_index is None:
            raise ValueError("nvenc backend requested but no AV1-capable GPU is available")
        cmd += _nvenc_video_args(preset, gpu_index, cap_bps)
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
    if video.get("color_range") in ("tv", "pc"):
        cmd += ["-color_range", video["color_range"]]

    cmd += _audio_args(kept_audio)
    cmd += _meaningful_stream_metadata_args("v", [video])
    cmd += _meaningful_stream_metadata_args("a", kept_audio)
    cmd += _disposition_args("a", len(kept_audio))

    if kept_subtitles:
        cmd += ["-c:s", "copy"]
        cmd += _disposition_args("s", len(kept_subtitles))
    cmd += ["-c:t", "copy"]

    if cover_image_path is not None:
        cmd += cover_art_args(cover_image_path, probed.get("attachment_count", 0))

    cmd.append(str(output_path))
    return cmd


def cover_art_args(cover_image_path: str | Path, existing_attachment_count: int) -> list[str]:
    """A cover image is a real Matroska *attachment* (`-attach`), not a
    disposition-flagged video stream -- ffmpeg accepts `-disposition:v:N
    attached_pic` without error, but it's silently a no-op for MKV output
    (confirmed directly: mkvmerge showed attached_pic=0 either way). The
    correct mechanism, confirmed with mkvmerge --identify, is `-attach`.

    ffmpeg requires an explicit mimetype for any attachment (errors with
    "has no mimetype tag and it cannot be deduced from the codec id"
    otherwise) and addresses it by *attachment-stream* index -- one past
    however many attachments the source already had (fonts mapped via
    `0:t?`), not always 0 -- so `existing_attachment_count` (from
    `probed["attachment_count"]`) has to be threaded through to land the
    metadata on the right stream."""
    cover_image_path = Path(cover_image_path)
    mimetype = _IMAGE_MIMETYPES.get(cover_image_path.suffix.lower(), "image/jpeg")
    new_index = existing_attachment_count
    return [
        "-attach",
        str(cover_image_path),
        f"-metadata:s:t:{new_index}",
        "mimetype=" + mimetype,
        f"-metadata:s:t:{new_index}",
        "filename=cover" + cover_image_path.suffix.lower(),
    ]


def _disposition_args(stream_type: str, count: int) -> list[str]:
    """Mark the first kept stream of this type as the player's default and
    explicitly clear the rest, so exactly one track auto-selects on
    playback -- rather than leaving it to whatever (possibly stale or
    wrong) disposition flags the source happened to carry."""
    if count == 0:
        return []
    args = [f"-disposition:{stream_type}:0", "default"]
    for i in range(1, count):
        args += [f"-disposition:{stream_type}:{i}", "0"]
    return args


def _svtav1_video_args(
    video: dict,
    preset: presets.Preset,
    hdr: bool,
    dolby_vision: bool,
    max_bitrate_bps: int | None,
    implementation: SvtImplementation,
) -> list[str]:
    selected_crf = presets.svt_crf(preset, implementation)
    if selected_crf is None:
        raise ValueError(
            "cannot identify FFmpeg's SVT-AV1 implementation; refusing a CPU encode "
            "because upstream and svt-av1-hdr use different CRF scales"
        )
    params = dict(preset.svt_extra)
    # Pin the fork's quality-relevant defaults. Its defaults have changed
    # independently of upstream before; explicit values make a dry-run
    # reproducible instead of silently changing after a package update.
    params.update(implementation_params(implementation, video.get("color_transfer")))
    params["tune"] = str(preset.svt_tune)
    if preset.film_grain:
        params["film-grain"] = str(preset.film_grain)
        # SVT-AV1 4.x defaults this to 0. Without the explicit 1, the
        # expensive source grain remains in the coded frames and synthesized
        # grain is signaled on top of it. That defeated the entire efficiency
        # reason grain-aware routing selected the CPU backend.
        params["film-grain-denoise"] = "1" if preset.film_grain_denoise else "0"
    if hdr:
        params.update(colorinfo.svtav1_hdr_params(video))
    if max_bitrate_bps:
        # SvtAv1EncApp's --mbr wants kbps; setting it alongside --crf puts
        # the encoder in "Capped CRF" mode (confirmed via its own startup
        # log: "BRC mode ... capped CRF").
        params["mbr"] = str(max_bitrate_bps // 1000)

    args = [
        "-c:v",
        "libsvtav1",
        "-preset",
        str(preset.svt_preset),
        "-crf",
        str(selected_crf),
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


def _nvenc_video_args(
    preset: presets.Preset, gpu_index: int, max_bitrate_bps: int | None
) -> list[str]:
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
    if max_bitrate_bps:
        # "Capped VBR": -cq still drives quality, -maxrate/-bufsize just
        # clamp the ceiling. Confirmed directly: uncapped CQ=22 on a real
        # clip produced ~17.9 Mbps; adding -maxrate 4M -bufsize 8M brought
        # it down to ~4.4 Mbps.
        args += ["-maxrate", str(max_bitrate_bps), "-bufsize", str(max_bitrate_bps * 2)]
    for key, value in preset.nvenc_extra.items():
        args += [f"-{key}", value]
    return args


def _audio_args(audio_streams: list[dict]) -> list[str]:
    args = []
    for i, stream in enumerate(audio_streams):
        bitrate = presets.opus_bitrate_kbps(stream["channels"])
        args += [f"-c:a:{i}", "libopus", f"-b:a:{i}", f"{bitrate}k"]
    return args


def _meaningful_stream_metadata_args(stream_type: str, streams: list[dict]) -> list[str]:
    """Restore human-authored identity fields after disabling automatic
    metadata copy. Codec-derived BPS/DURATION/NUMBER_OF_* statistics are
    deliberately not restored because the muxer cannot recalculate them."""
    args: list[str] = []
    for i, stream in enumerate(streams):
        for key in ("language", "title"):
            value = stream.get(key)
            if value:
                args += [f"-metadata:s:{stream_type}:{i}", f"{key}={value}"]
    return args
