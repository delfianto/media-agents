from __future__ import annotations

import pytest

from psammophis.compare.video import compare_video_files, require_positive, select_ssimulacra2


def test_require_positive_rejects_bad_values():
    with pytest.raises(ValueError):
        require_positive("clips", 0)
    with pytest.raises(ValueError):
        require_positive("clips", float("nan"))


def test_select_ssimulacra2_skip_and_require(monkeypatch):
    monkeypatch.setattr("psammophis.compare.video.find_ssimulacra2", lambda: None)
    binary, status = select_ssimulacra2(skip=True, require=False)
    assert binary is None
    assert "skipped" in status
    with pytest.raises(RuntimeError, match="ssimulacra2"):
        select_ssimulacra2(skip=False, require=True)


def test_compare_video_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="unknown compare mode"):
        compare_video_files(
            tmp_path / "a.mkv",
            tmp_path / "b.mkv",
            tmp_path,
            mode="not-a-mode",
        )
