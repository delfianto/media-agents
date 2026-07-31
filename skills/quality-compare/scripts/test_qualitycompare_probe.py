from qualitycompare.probe import VideoInfo, validate_alignment


def _info(**overrides) -> VideoInfo:
    values = {
        "path": "file.mkv",
        "duration": 100.0,
        "size": 1000,
        "bit_rate": 80,
        "width": 3840,
        "height": 2160,
        "frame_rate": 24000 / 1001,
        "frame_count": 2400,
        "pix_fmt": "yuv420p10le",
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "color_range": "tv",
    }
    values.update(overrides)
    return VideoInfo(**values)


def test_matching_video_passes_alignment():
    assert validate_alignment(_info(), _info(path="encode.mkv")) == []


def test_resolution_frame_count_and_transfer_mismatches_are_reported():
    errors = validate_alignment(
        _info(),
        _info(width=1920, height=1080, frame_count=2399, color_transfer="bt709"),
    )
    assert any("resolution differs" in error for error in errors)
    assert any("frame count differs" in error for error in errors)
    assert any("transfer differs" in error for error in errors)


def test_large_duration_difference_is_rejected():
    errors = validate_alignment(_info(), _info(duration=102))
    assert any("duration differs" in error for error in errors)
