from __future__ import annotations

from av1transcode import command, presets


def _probed(*, video_bit_rate=None, audio=None, subtitles=None):
    return {
        "video": {
            "index": 0,
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p10le",
            "bit_rate": video_bit_rate,
            "color_primaries": None,
            "color_transfer": None,
            "color_space": None,
            "mastering_display": None,
            "content_light": None,
            "dolby_vision": None,
        },
        "audio": audio or [],
        "subtitles": subtitles or [],
        "format": {"duration": 100.0, "size": 1_000_000, "bit_rate": video_bit_rate},
    }


def _preset():
    return presets.PRESETS[("1080p", "film")]


def _audio(index, language, channels=2):
    return {"index": index, "codec_name": "truehd", "channels": channels, "language": language}


def _sub(index, language):
    return {"index": index, "codec_name": "subrip", "language": language}


def test_only_primary_video_stream_is_mapped():
    probed = _probed(audio=[_audio(1, "eng")])
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu")
    assert cmd.count("-map") >= 1
    assert "0:0" in cmd  # the video stream's own index


def test_audio_language_filter_only_maps_matching_tracks():
    probed = _probed(audio=[_audio(1, "eng"), _audio(2, "jpn"), _audio(3, "eng")])
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu", audio_lang="eng")
    assert "0:1" in cmd
    assert "0:3" in cmd
    assert "0:2" not in cmd
    # two kept audio tracks -> two sets of opus codec/bitrate args
    assert cmd.count("libopus") == 2


def test_audio_language_filter_falls_back_when_nothing_matches():
    probed = _probed(audio=[_audio(1, "jpn")])
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu", audio_lang="eng")
    assert "0:1" in cmd  # kept anyway via the fallback, not dropped to silence
    assert cmd.count("libopus") == 1


def test_subtitle_language_filter_only_maps_matching_tracks():
    probed = _probed(
        audio=[_audio(1, "eng")],
        subtitles=[_sub(2, "eng"), _sub(3, "fre")],
    )
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu", subtitle_lang="eng")
    assert "0:2" in cmd
    assert "0:3" not in cmd


def test_drop_subtitles_overrides_subtitle_lang():
    probed = _probed(audio=[_audio(1, "eng")], subtitles=[_sub(2, "eng")])
    cmd = command.build_command(
        "in.mkv",
        "out.mkv",
        probed,
        _preset(),
        "cpu",
        drop_subtitles=True,
        subtitle_lang="all",
    )
    assert "0:2" not in cmd
    assert "-c:s" not in cmd


def test_disposition_flags_mark_first_kept_track_default():
    probed = _probed(audio=[_audio(1, "eng"), _audio(3, "eng")])
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu")
    assert "-disposition:a:0" in cmd
    assert cmd[cmd.index("-disposition:a:0") + 1] == "default"
    assert "-disposition:a:1" in cmd
    assert cmd[cmd.index("-disposition:a:1") + 1] == "0"


def test_attachments_always_mapped_via_wildcard():
    probed = _probed(audio=[_audio(1, "eng")])
    cmd = command.build_command("in.mkv", "out.mkv", probed, _preset(), "cpu")
    assert "0:t?" in cmd


def test_bitrate_cap_applied_for_nvenc():
    probed = _probed(video_bit_rate=20_000_000, audio=[_audio(1, "eng")])
    cmd = command.build_command(
        "in.mkv", "out.mkv", probed, _preset(), "nvenc", gpu_index=0, max_bitrate_fraction=0.5
    )
    assert "-maxrate" in cmd
    assert cmd[cmd.index("-maxrate") + 1] == "10000000"
    assert "-bufsize" in cmd


def test_bitrate_cap_applied_for_cpu_via_mbr():
    probed = _probed(video_bit_rate=20_000_000, audio=[_audio(1, "eng")])
    cmd = command.build_command(
        "in.mkv", "out.mkv", probed, _preset(), "cpu", max_bitrate_fraction=0.5
    )
    svtav1_params = cmd[cmd.index("-svtav1-params") + 1]
    assert "mbr=10000" in svtav1_params  # kbps


def test_no_bitrate_cap_when_fraction_is_none():
    probed = _probed(video_bit_rate=20_000_000, audio=[_audio(1, "eng")])
    cmd = command.build_command(
        "in.mkv", "out.mkv", probed, _preset(), "nvenc", gpu_index=0, max_bitrate_fraction=None
    )
    assert "-maxrate" not in cmd


def test_no_bitrate_cap_when_source_bitrate_unknown():
    probed = _probed(video_bit_rate=None, audio=[_audio(1, "eng")])
    probed["format"] = {"duration": None, "size": None, "bit_rate": None}
    cmd = command.build_command(
        "in.mkv", "out.mkv", probed, _preset(), "nvenc", gpu_index=0, max_bitrate_fraction=0.5
    )
    assert "-maxrate" not in cmd
