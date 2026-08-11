"""Denoise -> `av1_nvenc` encode -> `grav1synth diff`/`apply` pipeline: an
alternative to the CPU/libsvtav1 path for grainy sources that trades some of
SVT-AV1's own film-grain-synthesis efficiency for `nvenc`'s much higher
speed, by doing the denoise-and-resynthesize split as separate steps instead
of inside one encoder. See notes/av1-encoding.md's "Denoise + nvenc +
grav1synth: an nvenc-speed alternative to grain-aware CPU routing" section
for the investigation that validated (and the real bugs that blocked) this.

Deliberately narrow for its first release:

- SDR/HDR10 only. Dolby Vision and HDR10+ are refused outright -- neither
  this module nor `grav1synth` has been validated against them, and this
  repo's own culture (see root AGENTS.md) is to error rather than silently
  drop metadata a source actually carries.
- Never selected by `--backend auto`. Explicit `--backend nvenc-degrain`
  only -- `av1_backend.choose_backend`'s grain-routing decision (shared with
  the read-only `analyze` skill) is untouched by this module on purpose, so
  a much less battle-tested path can't silently become anyone's default.
- `grav1synth apply` was confirmed (see vendor-patches/grav1synth/README.md)
  to corrupt output when audio/subtitle streams are muxed alongside video in
  the same file, even though the exact same operation is fine on a video-only
  file. Every `grav1synth` step here therefore only ever touches a video-only
  intermediate; audio/subtitles are pulled fresh from the original source in
  a separate final mux step, reusing command.py's own audio/subtitle
  argument builders so the result matches a normal transcode on every axis
  except how the video itself got encoded.

The denoise step writes an FFV1 *lossless* intermediate -- deliberately, even
though it costs real disk space (roughly source-sized) and encode time.
`grav1synth diff` computes the grain table from the actual pixel difference
between source and denoised; lossy compression artifacts in the denoised
side would get baked into that difference and misread as grain, degrading
the model precisely for the noisy/detailed regions the whole point is to
capture accurately. FFV1 was chosen over an alternative like 10-bit
libx264 lossless because it doesn't depend on a specific libx264 build
flag (`--bit-depth=10`) that isn't guaranteed present -- silently falling
back to 8-bit or failing outright on some systems is worse than the disk
cost here.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from psammophis.medialib import av1_presets as presets
from psammophis.medialib import colorinfo

from . import command as command_mod
from . import langfilter

# Duplicated from run.py rather than imported: run.py imports this module at
# load time to call run_pipeline, so a top-level import the other way would
# be circular. These are trivial one-line Callable aliases, not worth a
# third shared module just to deduplicate three lines.
StructuredProgressCallback = Callable[[dict[str, float | str | None]], None]
PhaseCallback = Callable[[str, str], None]
HeartbeatCallback = Callable[[str], None]

DEGRAIN_BACKEND = "nvenc-degrain"

# hqdn3d params: luma_spatial, chroma_spatial, luma_tmp, chroma_tmp. Tuned
# once against real grainy 2160p HDR content (see notes/av1-encoding.md) --
# not exposed as a CLI knob yet, same "don't add an axis nothing has
# validated" reasoning as the DV refusal above.
_DENOISE_FILTER = "hqdn3d=8:8:6:6"

# photon-noise ISO strength grav1synth's `apply --iso` uses when a real
# diff-derived grain table isn't available; kept as a documented fallback
# only, not the default path -- see `run_pipeline`'s `grain_table` handling.
_FALLBACK_ISO = 400


def grav1synth_available() -> bool:
    return shutil.which("grav1synth") is not None


def _color_tag_args(video: dict) -> list[str]:
    args: list[str] = []
    if video.get("color_primaries"):
        args += ["-color_primaries", video["color_primaries"]]
    if video.get("color_transfer"):
        args += ["-color_trc", video["color_transfer"]]
    if video.get("color_space"):
        args += ["-colorspace", video["color_space"]]
    if video.get("color_range") in ("tv", "pc"):
        args += ["-color_range", video["color_range"]]
    return args


def build_denoise_command(
    source_path: str | Path, video: dict, denoised_output_path: str | Path
) -> list[str]:
    """Video-only, lossless FFV1 denoise pass. Frame count/timing must stay
    exactly 1:1 with the source -- `grav1synth diff` aligns the two inputs
    positionally, not by timestamp."""
    return [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(source_path),
        "-map",
        f"0:{video['index']}",
        "-vf",
        _DENOISE_FILTER,
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "yuv420p10le",
        *_color_tag_args(video),
        str(denoised_output_path),
    ]


def build_pregrain_encode_command(
    denoised_input_path: str | Path,
    output_path: str | Path,
    preset: presets.Preset,
    gpu_index: int,
    video: dict,
    max_bitrate_bps: int | None,
) -> list[str]:
    """`av1_nvenc` encode of the already-denoised, video-only intermediate.
    Reuses command.py's own nvenc argument builder so the actual encoder
    settings (preset/cq/AQ/bitrate cap) can never quietly drift from what a
    normal nvenc transcode would use."""
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(denoised_input_path),
        "-map",
        "0:v:0",
    ]
    cmd += command_mod.nvenc_video_args(preset, gpu_index, max_bitrate_bps)
    cmd += _color_tag_args(video)
    cmd.append(str(output_path))
    return cmd


def build_diff_command(
    source_path: str | Path, denoised_path: str | Path, grain_table_path: str | Path
) -> list[str]:
    return [
        "grav1synth",
        "diff",
        str(source_path),
        str(denoised_path),
        "-o",
        str(grain_table_path),
        "-y",
    ]


def build_apply_command(
    pregrain_video_path: str | Path,
    grained_output_path: str | Path,
    grain_table_path: str | Path | None,
) -> list[str]:
    """`grain_table_path=None` falls back to `--iso` photon-noise-based
    synthesis instead of a real diff-derived table -- only reached if the
    diff step failed to produce a usable table; see `run_pipeline`."""
    cmd = ["grav1synth", "apply", str(pregrain_video_path)]
    if grain_table_path is not None:
        cmd += ["--grain", str(grain_table_path)]
    else:
        cmd += ["--iso", str(_FALLBACK_ISO)]
    cmd += ["-o", str(grained_output_path), "-y"]
    return cmd


def build_mux_command(
    source_path: str | Path,
    grained_video_path: str | Path,
    output_path: str | Path,
    probed: dict,
    drop_subtitles: bool,
    audio_lang: str,
    subtitle_lang: str,
    single_audio_track: bool,
    cover_image_path: str | Path | None,
) -> list[str]:
    """Final container: video stream-copied from the grain-injected,
    already-fully-encoded file (input 0); audio/subtitles pulled fresh from
    the original source (input 1) and processed exactly like a normal
    transcode -- same language filtering, Opus re-encode, disposition flags,
    and metadata handling, via command.py's own builders."""
    kept_audio, _fallback = langfilter.filter_audio(
        probed.get("audio", []), audio_lang, single=single_audio_track
    )
    kept_subtitles = (
        []
        if drop_subtitles
        else langfilter.filter_subtitles(probed.get("subtitles", []), subtitle_lang)
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(grained_video_path),
        "-i",
        str(source_path),
    ]
    cmd += ["-map", "0:v:0", "-c:v", "copy"]
    for s in kept_audio:
        cmd += ["-map", f"1:{s['index']}"]
    for s in kept_subtitles:
        cmd += ["-map", f"1:{s['index']}"]
    cmd += ["-map", "1:t?"]
    cmd += [
        "-map_metadata",
        "-1",
        "-map_metadata:s:a",
        "-1",
        "-map_chapters",
        "1",
    ]
    cmd += command_mod.audio_args(kept_audio)
    cmd += command_mod.meaningful_stream_metadata_args("a", kept_audio)
    cmd += command_mod.disposition_args("a", len(kept_audio))
    if kept_subtitles:
        cmd += ["-c:s", "copy"]
        cmd += command_mod.disposition_args("s", len(kept_subtitles))
    cmd += ["-c:t", "copy"]
    if cover_image_path is not None:
        cmd += command_mod.cover_art_args(cover_image_path, probed.get("attachment_count", 0))
    cmd.append(str(output_path))
    return cmd


def check_supported(video: dict) -> None:
    """Raises ValueError for anything this pipeline hasn't been validated
    against, rather than attempting it and producing a subtly wrong result."""
    if colorinfo.has_dolby_vision(video):
        raise ValueError(
            "source has Dolby Vision metadata; the nvenc-degrain pipeline has not been "
            "validated against DV RPU preservation through grav1synth -- use --backend "
            "cpu (libsvtav1 supports it) or plain --backend nvenc with nvencc"
        )
    if colorinfo.needs_dynamic_metadata_path(video):
        raise ValueError(
            "source has HDR10+ dynamic metadata, which plain av1_nvenc cannot preserve "
            "and this pipeline does not route through nvencc -- use --backend cpu"
        )
    if not grav1synth_available():
        raise ValueError(
            "nvenc-degrain requires grav1synth on PATH -- not a standard package; build "
            "it from .agents/vendor-patches/grav1synth/README.md (upstream "
            "rust-av/grav1synth plus the two local patches fixing its av1_nvenc-specific "
            "corruption bug)"
        )


def _run_step(
    cmd: list[str],
    log_path: Path,
    phase: str,
    total_duration: float | None,
    on_progress: StructuredProgressCallback | None,
    on_phase: PhaseCallback | None,
    on_heartbeat: HeartbeatCallback | None,
) -> tuple[int, str]:
    from .run import stream_process  # local import: avoids a run.py <-> degrain.py cycle

    if on_phase is not None:
        on_phase(phase, "started")
    returncode, tail = stream_process(
        cmd,
        log_path,
        total_duration,
        on_progress=on_progress,
        on_heartbeat=(lambda: on_heartbeat(phase)) if on_heartbeat else None,
    )
    if on_phase is not None:
        on_phase(phase, "succeeded" if returncode == 0 else "failed")
    return returncode, tail


def run_pipeline(
    abs_path: Path,
    tmp_path: Path,
    probed: dict,
    preset: presets.Preset,
    gpu_index: int,
    max_bitrate_fraction: float | None,
    drop_subtitles: bool,
    audio_lang: str,
    subtitle_lang: str,
    single_audio_track: bool,
    cover_image_path: Path | None,
    log_dir: Path,
    on_progress: StructuredProgressCallback | None = None,
    on_phase: PhaseCallback | None = None,
    on_heartbeat: HeartbeatCallback | None = None,
) -> tuple[int, str]:
    """Runs the full denoise -> nvenc -> diff -> apply -> mux pipeline,
    writing the final result to `tmp_path`. Returns `(returncode, tail)`
    matching `run.stream_process`'s contract, so the caller in run.py can
    treat this exactly like a single-command encode -- 0 means `tmp_path` is
    a complete, verified-later output; nonzero means failure, with `tail`
    holding the last diagnostic output from whichever step failed.

    All intermediate files (denoised FFV1, pre-grain nvenc encode, grain
    table, grain-injected video-only file) are temporary and removed before
    returning, on both success and failure -- only `tmp_path` survives.
    """
    video = probed["video"]
    check_supported(video)

    duration = probed["format"].get("duration")
    cap_bps = (
        presets.max_bitrate_bps(probed, max_bitrate_fraction)
        if max_bitrate_fraction is not None
        else None
    )

    stem = tmp_path.name.removesuffix(".transcode-tmp.mkv")
    denoised_path = tmp_path.parent / f"{stem}.degrain-denoised.mkv"
    pregrain_path = tmp_path.parent / f"{stem}.degrain-pregrain.mkv"
    grain_table_path = tmp_path.parent / f"{stem}.degrain-grain.tbl"
    grained_video_path = tmp_path.parent / f"{stem}.degrain-video.mkv"
    temp_paths = (denoised_path, pregrain_path, grain_table_path, grained_video_path)

    try:
        rc, tail = _run_step(
            build_denoise_command(abs_path, video, denoised_path),
            log_dir / f"{tmp_path.stem}.denoise.log",
            "denoise",
            duration,
            on_progress,
            on_phase,
            on_heartbeat,
        )
        if rc != 0:
            return rc, tail

        rc, tail = _run_step(
            build_pregrain_encode_command(
                denoised_path, pregrain_path, preset, gpu_index, video, cap_bps
            ),
            log_dir / f"{tmp_path.stem}.nvenc-encode.log",
            "nvenc-encode",
            duration,
            on_progress,
            on_phase,
            on_heartbeat,
        )
        if rc != 0:
            return rc, tail

        rc, tail = _run_step(
            build_diff_command(abs_path, denoised_path, grain_table_path),
            log_dir / f"{tmp_path.stem}.grain-diff.log",
            "grain-diff",
            duration,
            on_progress,
            on_phase,
            on_heartbeat,
        )
        # A failed diff is recoverable -- fall back to synthetic photon-noise
        # grain rather than failing the whole encode outright, matching this
        # repo's "don't go silent/all-or-nothing when a softer fallback
        # exists" pattern elsewhere (e.g. langfilter's audio fallback).
        table_for_apply = grain_table_path if rc == 0 and grain_table_path.exists() else None

        rc, tail = _run_step(
            build_apply_command(pregrain_path, grained_video_path, table_for_apply),
            log_dir / f"{tmp_path.stem}.grain-apply.log",
            "grain-apply",
            duration,
            on_progress,
            on_phase,
            on_heartbeat,
        )
        if rc != 0:
            return rc, tail

        rc, tail = _run_step(
            build_mux_command(
                abs_path,
                grained_video_path,
                tmp_path,
                probed,
                drop_subtitles,
                audio_lang,
                subtitle_lang,
                single_audio_track,
                cover_image_path,
            ),
            log_dir / f"{tmp_path.stem}.mux.log",
            "mux",
            duration,
            on_progress,
            on_phase,
            on_heartbeat,
        )
        return rc, tail
    finally:
        for p in temp_paths:
            p.unlink(missing_ok=True)
