from __future__ import annotations

from pathlib import Path

import pytest

from psammophis.runtime.roots import (
    RootError,
    resolve_default_root,
    validate_deletion_target,
    validate_root,
)


def test_resolve_prefers_feature_env():
    root = resolve_default_root(
        feature_env="TRANSCODE_ROOT",
        environ={
            "TRANSCODE_ROOT": "/media/a",
            "MEDIALIB_ROOT": "/media/b",
        },
        cwd=Path("/tmp"),
    )
    assert root.path == Path("/media/a")
    assert root.source == "TRANSCODE_ROOT"


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


def test_validate_root_rejects_home_directory():
    with pytest.raises(RootError, match="dangerously broad"):
        validate_root(Path.home())


def test_deletion_target_rejects_symlink(tmp_path):
    root = tmp_path / "library"
    target = tmp_path / "outside"
    root.mkdir()
    target.mkdir()
    link = root / ".cache" / "backups"
    link.parent.mkdir()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RootError, match="through symlink"):
        validate_deletion_target(link, media_root=root)


def test_deletion_target_rejects_media_root_parent_and_protected_state(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(RootError, match="media root"):
        validate_deletion_target(root, media_root=root)
    with pytest.raises(RootError, match="media root"):
        validate_deletion_target(tmp_path, media_root=root)
    with pytest.raises(RootError, match="protected application state"):
        validate_deletion_target(root / ".cache", media_root=root)


def test_deletion_target_rejects_protected_state_descendant(tmp_path):
    root = tmp_path / "library"
    target = root / ".cache" / "psammophis" / "runs" / "one"
    target.mkdir(parents=True)
    with pytest.raises(RootError, match="protected application state"):
        validate_deletion_target(target, media_root=root)


def test_deletion_target_rejects_symlinked_ancestor(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    target = linked / "backups"
    target.mkdir()
    with pytest.raises(RootError, match="through symlink"):
        validate_deletion_target(target)


def test_deletion_target_accepts_exact_nested_backup(tmp_path):
    root = tmp_path / "library"
    backup = root / ".cache" / "transcode" / "originals"
    backup.mkdir(parents=True)
    assert validate_deletion_target(backup, media_root=root) == backup.resolve()
