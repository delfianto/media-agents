from __future__ import annotations

from pathlib import Path

import pytest

from psammophis.runtime.roots import RootError, resolve_default_root, validate_root


def test_resolve_prefers_feature_env():
    root = resolve_default_root(
        feature_env="AV1TRANSCODE_ROOT",
        environ={
            "AV1TRANSCODE_ROOT": "/media/a",
            "MEDIALIB_ROOT": "/media/b",
        },
        cwd=Path("/tmp"),
    )
    assert root.path == Path("/media/a")
    assert root.source == "AV1TRANSCODE_ROOT"


def test_resolve_falls_back_to_medialib_root():
    root = resolve_default_root(
        feature_env="TRACKSTRIP_ROOT",
        environ={"MEDIALIB_ROOT": "/media/lib"},
        cwd=Path("/tmp"),
    )
    assert root.path == Path("/media/lib")
    assert root.source == "MEDIALIB_ROOT"


def test_resolve_falls_back_to_cwd(tmp_path):
    root = resolve_default_root(environ={}, cwd=tmp_path)
    assert root.path == tmp_path
    assert root.source == "cwd"


def test_validate_root_rejects_missing(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(RootError, match="does not exist"):
        validate_root(missing)


def test_validate_root_rejects_file(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(RootError, match="not a directory"):
        validate_root(file_path)


def test_validate_root_rejects_filesystem_root():
    with pytest.raises(RootError, match="dangerously broad"):
        validate_root("/", must_exist=True)


def test_validate_root_accepts_real_directory(tmp_path):
    assert validate_root(tmp_path) == tmp_path.resolve()
