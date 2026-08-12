from __future__ import annotations

import pytest

from psammophis.medialib import av1_presets as presets
from psammophis.transcode import degrain


def _video(**overrides):
    video = {
        "index": 0,
        "codec_name": "hevc",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "bit_rate": None,
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "color_range": "tv",
        "mastering_display": None,
        "content_light": None,
        "dolby_vision": None,
        "hdr10_plus": None,
    }
    video.update(overrides)
    return video


def _probed(video=None, *, audio=None, subtitles=None, attachment_count=0):
    return {
        "video": video or _video(),
        "audio": audio or [],
        "subtitles": subtitles or [],
        "format": {"duration": 100.0, "size": 1_000_000, "bit_rate": 20_000_000},
        "attachment_count": attachment_count,
    }


def _preset():
    return presets.PRESETS[("2160p", "film")]


def _audio_stream(index, language="eng", channels=2, channel_layout=None):
    return {
        "index": index,
        "codec_name": "eac3",
        "profile": None,
        "channels": channels,
        "channel_layout": channel_layout,
        "bit_rate": None,
        "language": language,
        "title": None,
    }


def _subtitle_stream(index, language="eng", hearing_impaired=False, title=None):
    return {
        "index": index,
        "codec_name": "subrip",
        "language": language,
        "title": title,
        "hearing_impaired": hearing_impaired,
    }


# ===== build_denoise_command =====


def test_build_denoise_command_is_video_only_and_lossless():
    video = _video()
    cmd = degrain.build_denoise_command("/src.mkv", video, "/tmp/denoised.mkv")
    assert cmd[0] == "ffmpeg"
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:0"
    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "ffv1"
    assert "-vf" in cmd
    assert cmd[-1] == "/tmp/denoised.mkv"


def test_build_denoise_command_preserves_color_tags():
    video = _video(color_primaries="bt2020", color_transfer="smpte2084", color_space="bt2020nc")
    cmd = degrain.build_denoise_command("/src.mkv", video, "/tmp/denoised.mkv")
    assert "-color_primaries" in cmd
    assert cmd[cmd.index("-color_primaries") + 1] == "bt2020"
    assert cmd[cmd.index("-color_trc") + 1] == "smpte2084"
    assert cmd[cmd.index("-colorspace") + 1] == "bt2020nc"


def test_build_denoise_command_omits_absent_color_tags():
    video = _video(color_primaries=None, color_transfer=None, color_space=None, color_range=None)
    cmd = degrain.build_denoise_command("/src.mkv", video, "/tmp/denoised.mkv")
    assert "-color_primaries" not in cmd
    assert "-color_trc" not in cmd
    assert "-colorspace" not in cmd
    assert "-color_range" not in cmd


# ===== build_pregrain_encode_command =====


def test_build_pregrain_encode_command_reuses_nvenc_video_args():
    from psammophis.transcode import command as command_mod

    video = _video()
    preset = _preset()
    cmd = degrain.build_pregrain_encode_command(
        "/tmp/denoised.mkv",
        "/tmp/pregrain.mkv",
        preset,
        gpu_index=0,
        video=video,
        max_bitrate_bps=None,
    )
    expected_video_args = command_mod.nvenc_video_args(preset, 0, None)
    for arg in expected_video_args:
        assert arg in cmd


def test_build_pregrain_encode_command_maps_video_only():
    cmd = degrain.build_pregrain_encode_command(
        "/tmp/denoised.mkv",
        "/tmp/pregrain.mkv",
        _preset(),
        gpu_index=0,
        video=_video(),
        max_bitrate_bps=None,
    )
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[-1] == "/tmp/pregrain.mkv"


def test_build_pregrain_encode_command_applies_bitrate_cap():
    cmd = degrain.build_pregrain_encode_command(
        "/tmp/denoised.mkv",
        "/tmp/pregrain.mkv",
        _preset(),
        gpu_index=0,
        video=_video(),
        max_bitrate_bps=4_000_000,
    )
    assert "-maxrate" in cmd
    assert cmd[cmd.index("-maxrate") + 1] == "4000000"


# ===== build_diff_command / build_apply_command =====


def test_build_diff_command():
    cmd = degrain.build_diff_command("/src.mkv", "/tmp/denoised.mkv", "/tmp/grain.tbl")
    assert cmd == [
        "grav1synth",
        "diff",
        "/src.mkv",
        "/tmp/denoised.mkv",
        "-o",
        "/tmp/grain.tbl",
        "-y",
    ]


def test_build_apply_command_with_grain_table():
    cmd = degrain.build_apply_command("/tmp/pregrain.mkv", "/tmp/grained.mkv", "/tmp/grain.tbl")
    assert "--grain" in cmd
    assert cmd[cmd.index("--grain") + 1] == "/tmp/grain.tbl"
    assert "--iso" not in cmd


def test_build_apply_command_falls_back_to_iso_without_a_table():
    cmd = degrain.build_apply_command("/tmp/pregrain.mkv", "/tmp/grained.mkv", None)
    assert "--iso" in cmd
    assert "--grain" not in cmd


# ===== build_mux_command =====


def test_build_mux_command_video_is_stream_copied_from_grained_file():
    probed = _probed(audio=[_audio_stream(1)], subtitles=[_subtitle_stream(2)])
    cmd = degrain.build_mux_command(
        "/src.mkv",
        "/tmp/grained.mkv",
        "/out.mkv",
        probed,
        drop_subtitles=False,
        audio_lang="eng",
        subtitle_lang="eng",
        single_audio_track=True,
        cover_image_path=None,
    )
    assert cmd[cmd.index("-i") + 1] == "/tmp/grained.mkv"
    # second -i is the source, for audio/subtitles
    assert cmd.count("-i") == 2
    assert "/src.mkv" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_build_mux_command_maps_audio_and_subtitles_from_source_input():
    probed = _probed(audio=[_audio_stream(1)], subtitles=[_subtitle_stream(2)])
    cmd = degrain.build_mux_command(
        "/src.mkv",
        "/tmp/grained.mkv",
        "/out.mkv",
        probed,
        drop_subtitles=False,
        audio_lang="eng",
        subtitle_lang="eng",
        single_audio_track=True,
        cover_image_path=None,
    )
    assert "1:1" in cmd  # audio stream index 1, from input 1 (the source)
    assert "1:2" in cmd  # subtitle stream index 2, from input 1
    assert "-c:a:0" in cmd
    assert cmd[cmd.index("-c:a:0") + 1] == "libopus"
    assert "-c:s" in cmd
    assert cmd[cmd.index("-c:s") + 1] == "copy"


def test_build_mux_command_respects_drop_subtitles():
    probed = _probed(audio=[_audio_stream(1)], subtitles=[_subtitle_stream(2)])
    cmd = degrain.build_mux_command(
        "/src.mkv",
        "/tmp/grained.mkv",
        "/out.mkv",
        probed,
        drop_subtitles=True,
        audio_lang="eng",
        subtitle_lang="eng",
        single_audio_track=True,
        cover_image_path=None,
    )
    assert "1:2" not in cmd
    assert "-c:s" not in cmd


def test_build_mux_command_applies_cover_art():
    probed = _probed(audio=[_audio_stream(1)], attachment_count=0)
    cmd = degrain.build_mux_command(
        "/src.mkv",
        "/tmp/grained.mkv",
        "/out.mkv",
        probed,
        drop_subtitles=False,
        audio_lang="eng",
        subtitle_lang="eng",
        single_audio_track=True,
        cover_image_path="/poster.jpg",
    )
    assert "-attach" in cmd
    assert "/poster.jpg" in cmd


def test_build_mux_command_audio_copy_stream_copies_instead_of_opus():
    probed = _probed(audio=[_audio_stream(1)])
    cmd = degrain.build_mux_command(
        "/src.mkv",
        "/tmp/grained.mkv",
        "/out.mkv",
        probed,
        drop_subtitles=False,
        audio_lang="eng",
        subtitle_lang="eng",
        single_audio_track=True,
        cover_image_path=None,
        audio_copy=True,
    )
    assert cmd[cmd.index("-c:a:0") + 1] == "copy"
    assert "libopus" not in cmd
    assert "-b:a:0" not in cmd


# ===== check_supported =====


def test_check_supported_rejects_dolby_vision():
    video = _video(dolby_vision={"profile": 8})
    with pytest.raises(ValueError, match="Dolby Vision"):
        degrain.check_supported(video)


def test_check_supported_rejects_hdr10_plus():
    video = _video(hdr10_plus={"some": "data"})
    with pytest.raises(ValueError, match="HDR10\\+"):
        degrain.check_supported(video)


def test_check_supported_rejects_missing_grav1synth(monkeypatch):
    monkeypatch.setattr(degrain, "grav1synth_available", lambda: False)
    with pytest.raises(ValueError, match="grav1synth"):
        degrain.check_supported(_video())


def test_check_supported_passes_for_sdr_with_grav1synth(monkeypatch):
    monkeypatch.setattr(degrain, "grav1synth_available", lambda: True)
    degrain.check_supported(_video())  # must not raise


# ===== grav1synth_available =====


def test_grav1synth_available_reflects_path(monkeypatch):
    monkeypatch.setattr(degrain.shutil, "which", lambda name: None)
    assert degrain.grav1synth_available() is False
    monkeypatch.setattr(degrain.shutil, "which", lambda name: "/usr/bin/grav1synth")
    assert degrain.grav1synth_available() is True
