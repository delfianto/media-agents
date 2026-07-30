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
        "audio": [{"index": 1}],
        "format": {"duration": duration, "size": size},
    }


def _patch_new_probed(monkeypatch, new_video, *, duration=9326.6, size=45_000_000_000):
    new_probed = _probed(new_video, duration=duration, size=size)
    monkeypatch.setattr(run_mod, "probe_file", lambda path: new_probed)
    monkeypatch.setattr(run_mod, "_decode_spot_check", lambda path: (True, "ok"))


def test_verify_output_passes_when_dv_present_and_color_tags_intact(monkeypatch, tmp_path):
    orig = _probed(_video(dolby_vision={"dv_profile": 7}))
    _patch_new_probed(monkeypatch, _video(dolby_vision={"dv_profile": 10}))
    ok, detail = run_mod.verify_output(orig, tmp_path / "out.mkv")
    assert ok
    assert detail == "ok"


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
