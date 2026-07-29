from __future__ import annotations

from av1transcode import colorinfo, nvencc_cmd, presets


def _probed(*, video_extra=None, audio=None, subtitles=None):
    video = {
        "index": 0,
        "codec_name": "hevc",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "bit_rate": 60_000_000,
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "mastering_display": {"max_luminance": "1000/1"},
        "content_light": None,
        "dolby_vision": None,
        "hdr10_plus": None,
    }
    if video_extra:
        video.update(video_extra)
    return {
        "video": video,
        "audio": audio
        or [
            {
                "index": 1,
                "codec_name": "truehd",
                "profile": None,
                "channels": 8,
                "bit_rate": None,
                "language": "eng",
            }
        ],
        "subtitles": subtitles
        or [
            {
                "index": 2,
                "codec_name": "subrip",
                "language": "eng",
                "hearing_impaired": False,
            }
        ],
        "format": {"duration": 100.0, "size": 1_000_000_000, "bit_rate": 60_000_000},
        "attachment_count": 0,
    }


def _preset():
    return presets.PRESETS[("2160p", "film")]


def test_stream_to_nvencc_track_number_is_1_based_among_type():
    streams = [
        {"index": 1, "language": "eng"},
        {"index": 3, "language": "jpn"},
        {"index": 5, "language": "eng"},
    ]
    assert nvencc_cmd.stream_to_nvencc_track_number(streams, streams[0]) == 1
    assert nvencc_cmd.stream_to_nvencc_track_number(streams, streams[2]) == 3


def test_map_nvenc_preset():
    assert nvencc_cmd.map_nvenc_preset("p7") == "quality"
    assert nvencc_cmd.map_nvenc_preset("p1") == "performance"


def test_build_nvencc_command_includes_dv_flags():
    probed = _probed(video_extra={"dolby_vision": {"dv_profile": 8}})
    cmd = nvencc_cmd.build_nvencc_command("in.mkv", "out.mkv", probed, _preset(), gpu_index=0)
    assert cmd[0] == "nvencc"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "av1"
    assert "--dolby-vision-rpu" in cmd
    assert cmd[cmd.index("--dolby-vision-rpu") + 1] == "copy"
    assert "--dolby-vision-profile" in cmd
    assert cmd[cmd.index("--dolby-vision-profile") + 1] == "10.1"
    assert "--dhdr10-info" not in cmd
    assert "--qvbr" in cmd
    assert "--max-bitrate" in cmd  # 85% of 60 Mbps source video


def test_build_nvencc_command_includes_hdr10_plus_flags():
    probed = _probed(video_extra={"hdr10_plus": {"side_data_type": "HDR10+ Metadata"}})
    cmd = nvencc_cmd.build_nvencc_command("in.mkv", "out.mkv", probed, _preset())
    assert "--dhdr10-info" in cmd
    assert cmd[cmd.index("--dhdr10-info") + 1] == "copy"
    assert "--dolby-vision-rpu" not in cmd


def test_build_nvencc_command_selects_single_best_eng_audio():
    probed = _probed(
        audio=[
            {
                "index": 1,
                "codec_name": "eac3",
                "profile": None,
                "channels": 6,
                "bit_rate": 640000,
                "language": "eng",
            },
            {
                "index": 2,
                "codec_name": "truehd",
                "profile": "Dolby TrueHD + Dolby Atmos",
                "channels": 8,
                "bit_rate": None,
                "language": "eng",
            },
            {
                "index": 3,
                "codec_name": "truehd",
                "profile": None,
                "channels": 8,
                "bit_rate": None,
                "language": "jpn",
            },
        ]
    )
    cmd = nvencc_cmd.build_nvencc_command("in.mkv", "out.mkv", probed, _preset(), audio_lang="eng")
    # TrueHD eng is audio track #2 among audio streams
    assert "--audio-copy" in cmd
    assert cmd[cmd.index("--audio-copy") + 1] == "2"
    assert "2?libopus" in cmd


def test_build_nvencc_command_external_rpu_path():
    probed = _probed(video_extra={"dolby_vision": {"dv_profile": 8}})
    cmd = nvencc_cmd.build_nvencc_command(
        "in.mkv",
        "out.mkv",
        probed,
        _preset(),
        dolby_vision_rpu="/tmp/RPU.bin",
        dolby_vision_profile="10.0",
    )
    assert cmd[cmd.index("--dolby-vision-rpu") + 1] == "/tmp/RPU.bin"
    assert cmd[cmd.index("--dolby-vision-profile") + 1] == "10.0"


def test_needs_dynamic_metadata_path():
    assert colorinfo.needs_dynamic_metadata_path({"dolby_vision": {}}) is True
    assert colorinfo.needs_dynamic_metadata_path({"hdr10_plus": {}}) is True
    assert colorinfo.needs_dynamic_metadata_path({}) is False


def test_has_hdr10_plus():
    assert colorinfo.has_hdr10_plus({"hdr10_plus": {"x": 1}}) is True
    assert colorinfo.has_hdr10_plus({"hdr10_plus": None}) is False
    assert colorinfo.has_hdr10_plus({}) is False
