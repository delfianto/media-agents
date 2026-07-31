"""Exit-code contract smoke for feature handlers (no real media mutation)."""

from __future__ import annotations

from psammophis.analyze import cli as analyze_cli
from psammophis.artwork import cli as artwork_cli
from psammophis.envcheck import cli as envcheck_cli
from psammophis.mkvedit import cli as mkvedit_cli
from psammophis.organize import cli as organize_cli
from psammophis.subtitle import cli as subtitle_cli
from psammophis.trackstrip import cli as trackstrip_cli
from psammophis.transcode import cli as transcode_cli


def test_analyze_empty_root_succeeds(tmp_path):
    (tmp_path / "Movies").mkdir()
    assert analyze_cli.main(["--root", str(tmp_path), "--limit", "0"]) == 0


def test_artwork_config_error_is_usage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Missing credentials / .env should be a configuration error (2)
    code = artwork_cli.main(["--path", str(tmp_path), "--env-file", "missing.env"])
    assert code == 2


def test_mkvedit_missing_path_is_usage(tmp_path):
    code = mkvedit_cli.main(["--path", str(tmp_path / "nope.mkv")])
    assert code == 2


def test_trackstrip_stats_without_cache(tmp_path):
    code = trackstrip_cli.main(
        ["--root", str(tmp_path), "--cache", str(tmp_path / "scan.json"), "stats"]
    )
    assert code == 1


def test_av1_run_empty_selection_exits_zero(tmp_path):
    (tmp_path / "Movies").mkdir()
    code = transcode_cli.main(["--root", str(tmp_path), "run", "--limit", "0"])
    assert code == 0


def test_purge_refuses_missing_media_root(tmp_path):
    missing = tmp_path / "unmounted-library"
    code = transcode_cli.main(
        [
            "--root",
            str(missing),
            "purge-backups",
            "--backup-dir",
            str(tmp_path / "some-backup"),
            "--yes",
        ]
    )
    assert code == 2


def test_envcheck_returns_int():
    code = envcheck_cli.main([])
    assert isinstance(code, int)
    assert code in (0, 1)


def test_organize_config_error_is_usage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = organize_cli.main(["--env-file", "missing.env"])
    assert code == 2


def test_subtitle_missing_api_key_on_apply_is_usage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = subtitle_cli.main(["--path", str(tmp_path), "--env-file", "missing.env", "--yes"])
    assert code == 2
