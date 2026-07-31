from __future__ import annotations

import pytest

from psammophis.runtime.signals import CancellationRequested
from psammophis.transcode import run as run_mod


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


def test_commit_refuses_different_existing_destination_without_touching_source(tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.transcode-tmp.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_text("original")
    temporary.write_text("verified")
    destination.write_text("existing")

    with pytest.raises(FileExistsError):
        run_mod._commit_in_place(source, temporary, destination, None, source.relative_to(tmp_path))

    assert source.read_text() == "original"
    assert temporary.read_text() == "verified"
    assert destination.read_text() == "existing"


def test_no_backup_different_extension_installs_before_deleting_source(tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.transcode-tmp.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_text("original")
    temporary.write_text("verified")

    run_mod._commit_in_place(source, temporary, destination, None, source.relative_to(tmp_path))

    assert not source.exists()
    assert not temporary.exists()
    assert destination.read_text() == "verified"


def test_output_directory_can_never_map_result_onto_source(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_text("original")

    result, probed = run_mod.transcode_one(
        source,
        tmp_path,
        "film",
        "cpu",
        None,
        None,
        True,
        tmp_path / "logs",
        output_dir=tmp_path,
        overwrite_existing=True,
    )

    assert result.status == "error"
    assert "onto its own source" in result.detail
    assert probed is None
    assert source.read_text() == "original"


def test_backup_commit_failure_leaves_original_in_place(monkeypatch, tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.transcode-tmp.mkv"
    destination = tmp_path / "movie.mkv"
    backup_root = tmp_path / "backups"
    source.write_text("original")
    temporary.write_text("verified")

    monkeypatch.setattr(
        run_mod,
        "install_verified",
        lambda _source, _temporary, _destination: (_ for _ in ()).throw(OSError("install failed")),
    )
    with pytest.raises(OSError, match="install failed"):
        run_mod._commit_in_place(
            source,
            temporary,
            destination,
            str(backup_root),
            source.relative_to(tmp_path),
        )

    assert source.read_text() == "original"
    assert temporary.read_text() == "verified"
    assert not destination.exists()
    assert not (backup_root / source.name).exists()


def test_cancellation_between_backup_and_commit_never_moves_original(monkeypatch, tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.transcode-tmp.mkv"
    destination = tmp_path / "movie.mkv"
    backup_root = tmp_path / "backups"
    source.write_text("original")
    temporary.write_text("verified")

    def cancel(_source, _temporary, _destination):
        raise CancellationRequested(15)

    monkeypatch.setattr(run_mod, "install_verified", cancel)
    with pytest.raises(CancellationRequested):
        run_mod._commit_in_place(
            source,
            temporary,
            destination,
            str(backup_root),
            source.relative_to(tmp_path),
        )

    assert source.read_text() == "original"
    assert temporary.read_text() == "verified"
    assert not destination.exists()
    assert not (backup_root / source.name).exists()


def test_backup_cleanup_failure_preserves_all_recovery_material(monkeypatch, tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.transcode-tmp.mkv"
    destination = tmp_path / "movie.mkv"
    backup_root = tmp_path / "backups"
    source.write_text("original")
    temporary.write_text("verified")
    monkeypatch.setattr(
        run_mod,
        "install_verified",
        lambda _source, _temporary, _destination: (_ for _ in ()).throw(OSError("install failed")),
    )
    monkeypatch.setattr(
        run_mod,
        "discard_staged_backup",
        lambda _backup: (_ for _ in ()).throw(run_mod.RecoveryRequired("backup cleanup failed")),
    )
    with pytest.raises(run_mod.RecoveryRequired, match="backup cleanup failed"):
        run_mod._commit_in_place(
            source,
            temporary,
            destination,
            str(backup_root),
            source.relative_to(tmp_path),
        )

    assert source.read_text() == "original"
    assert (backup_root / source.name).read_text() == "original"
    assert temporary.read_text() == "verified"


def test_cover_remux_preserves_an_existing_recovery_file(tmp_path):
    video = tmp_path / "encoded.mkv"
    cover = tmp_path / "poster.jpg"
    recovery = tmp_path / "encoded.cover-tmp.mkv"
    video.write_bytes(b"encoded")
    cover.write_bytes(b"cover")
    recovery.write_bytes(b"recover me")

    with pytest.raises(FileExistsError, match="temporary file already exists"):
        run_mod._attach_cover_remux(video, cover)

    assert video.read_bytes() == b"encoded"
    assert recovery.read_bytes() == b"recover me"
