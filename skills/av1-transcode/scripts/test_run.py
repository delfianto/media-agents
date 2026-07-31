from __future__ import annotations

from av1transcode import run as run_mod


def _video(**overrides):
    video = {
        "index": 0,
        "codec_name": "av1",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "bit_rate": None,
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "color_range": "tv",
        "mastering_display": {"max_luminance": "1000/1"},
        "content_light": None,
        "dolby_vision": None,
        "hdr10_plus": None,
    }
    video.update(overrides)
    return video


def _probed(video, *, duration=9326.6, size=45_000_000_000):
    return {
        "video": video,
        "audio": [{"index": 1, "statistics_tags": {}}],
        "format": {"duration": duration, "size": size},
    }


def _patch_new_probed(monkeypatch, new_video, *, duration=9326.6, size=45_000_000_000):
    new_video.setdefault(
        "statistics_tags",
        {"BPS": "38000000", "NUMBER_OF_BYTES": "44000000000"},
    )
    new_probed = _probed(new_video, duration=duration, size=size)
    new_probed["audio"][0]["statistics_tags"] = {
        "BPS": "450000",
        "NUMBER_OF_BYTES": "500000000",
    }
    monkeypatch.setattr(run_mod, "probe_file", lambda path: new_probed)
    monkeypatch.setattr(run_mod, "_decode_spot_check", lambda path: (True, "ok"))


def test_verify_output_passes_when_dv_present_and_color_tags_intact(monkeypatch, tmp_path):
    orig = _probed(_video(dolby_vision={"dv_profile": 7}))
    _patch_new_probed(monkeypatch, _video(dolby_vision={"dv_profile": 10}))
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert ok
    assert "GiB" in detail
    assert "overall bitrate" in detail


def test_verify_output_rejects_copied_video_statistics(monkeypatch, tmp_path):
    stale = {"BPS-eng": "57537352", "NUMBER_OF_BYTES-eng": "30878657322"}
    orig = _probed(_video(statistics_tags=stale))
    _patch_new_probed(monkeypatch, _video(statistics_tags=stale), size=22_000_000_000)
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "stale source statistics" in detail


def test_verify_output_accepts_recalculated_equal_frame_count(monkeypatch, tmp_path):
    orig = _probed(
        _video(
            statistics_tags={
                "BPS-eng": "57537352",
                "NUMBER_OF_FRAMES-eng": "102938",
                "NUMBER_OF_BYTES-eng": "30878657322",
            }
        )
    )
    _patch_new_probed(
        monkeypatch,
        _video(
            statistics_tags={
                "BPS": "22000000",
                "NUMBER_OF_FRAMES": "102938",
                "NUMBER_OF_BYTES": "11700000000",
            }
        ),
        size=12_000_000_000,
    )
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert ok
    assert "smaller" in detail


def test_verify_output_rejects_missing_measured_statistics(monkeypatch, tmp_path):
    orig = _probed(_video())
    new_probed = _probed(_video(statistics_tags={}), size=22_000_000_000)
    monkeypatch.setattr(run_mod, "probe_file", lambda path: new_probed)
    monkeypatch.setattr(run_mod, "_decode_spot_check", lambda path: (True, "ok"))
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "missing measured track statistics" in detail


def test_verify_output_fails_when_dv_present_but_color_transfer_lost(monkeypatch, tmp_path):
    # The exact regression this used to let through: nvencc's DV path left
    # color_transfer "unknown" even with RPU/DOVI config intact, and the old
    # check skipped the color-tag verification entirely whenever DV was
    # present on the new stream -- see reference/incidents.md.
    orig = _probed(_video(dolby_vision={"dv_profile": 7}))
    _patch_new_probed(
        monkeypatch,
        _video(dolby_vision={"dv_profile": 10}, color_transfer=None, color_primaries=None),
    )
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "PQ/HLG" in detail


def test_verify_output_fails_when_dv_present_but_mastering_display_lost(monkeypatch, tmp_path):
    orig = _probed(_video(dolby_vision={"dv_profile": 7}))
    _patch_new_probed(
        monkeypatch,
        _video(dolby_vision={"dv_profile": 10}, mastering_display=None),
    )
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "mastering-display" in detail


def test_verify_output_fails_when_dv_dropped_entirely(monkeypatch, tmp_path):
    orig = _probed(_video(dolby_vision={"dv_profile": 7}))
    _patch_new_probed(monkeypatch, _video(dolby_vision=None))
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "DOVI configuration record" in detail


def test_verify_output_fails_when_sdr_hdr_output_loses_transfer(monkeypatch, tmp_path):
    orig = _probed(_video())
    _patch_new_probed(monkeypatch, _video(color_transfer=None, color_primaries=None))
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert not ok
    assert "PQ/HLG" in detail
