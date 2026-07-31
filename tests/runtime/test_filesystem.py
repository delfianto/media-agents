from __future__ import annotations

import errno
import os

import pytest

from psammophis.runtime import filesystem
from psammophis.runtime.signals import CancellationRequested


def test_staged_hardlink_backup_survives_atomic_source_replacement(tmp_path):
    source = tmp_path / "movie.mkv"
    backup = tmp_path / "backups" / "movie.mkv"
    replacement = tmp_path / ".replacement.mkv"
    source.write_bytes(b"original")
    replacement.write_bytes(b"verified")

    filesystem.stage_backup(source, backup)
    filesystem.install_verified(source, replacement, source)

    assert source.read_bytes() == b"verified"
    assert backup.read_bytes() == b"original"
    assert not replacement.exists()


def test_staged_backup_falls_back_to_exclusive_cross_device_copy(monkeypatch, tmp_path):
    source = tmp_path / "movie.mkv"
    backup = tmp_path / "backups" / "movie.mkv"
    source.write_bytes(b"original")
    real_link = filesystem.os.link

    def cross_device(left, right, **kwargs):
        if left == source and right == backup:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_link(left, right, **kwargs)

    monkeypatch.setattr(filesystem.os, "link", cross_device)
    filesystem.stage_backup(source, backup)
    source.write_bytes(b"changed in place")

    assert backup.read_bytes() == b"original"


def test_failed_backup_link_never_deletes_a_concurrent_destination(monkeypatch, tmp_path):
    source = tmp_path / "movie.mkv"
    backup = tmp_path / "backups" / "movie.mkv"
    source.write_bytes(b"original")
    real_link = filesystem.os.link

    def concurrent_link(left, right, **kwargs):
        real_link(left, right, **kwargs)
        raise FileExistsError(errno.EEXIST, "simulated concurrent destination")

    monkeypatch.setattr(filesystem.os, "link", concurrent_link)
    with pytest.raises(FileExistsError):
        filesystem.stage_backup(source, backup)

    assert source.read_bytes() == b"original"
    assert backup.read_bytes() == b"original"


def test_failed_no_replace_link_never_deletes_a_concurrent_destination(monkeypatch, tmp_path):
    source = tmp_path / ".complete.tmp"
    destination = tmp_path / "movie.mkv"
    source.write_bytes(b"complete")
    real_link = filesystem.os.link

    def concurrent_link(left, right, **kwargs):
        real_link(left, right, **kwargs)
        raise FileExistsError(errno.EEXIST, "simulated concurrent destination")

    monkeypatch.setattr(filesystem.os, "link", concurrent_link)
    with pytest.raises(FileExistsError):
        filesystem.install_no_replace(source, destination)

    assert source.read_bytes() == b"complete"
    assert destination.read_bytes() == b"complete"


def test_install_verified_rolls_back_new_destination_if_source_unlink_fails(monkeypatch, tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    temporary.write_bytes(b"verified")
    real_unlink = filesystem.Path.unlink

    def fail_source_unlink(path, *args, **kwargs):
        if path == source:
            raise OSError("source busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.Path, "unlink", fail_source_unlink)
    with pytest.raises(OSError, match="source busy"):
        filesystem.install_verified(source, temporary, destination)

    assert source.read_bytes() == b"original"
    assert temporary.read_bytes() == b"verified"
    assert not destination.exists()


def test_install_verified_cancellation_before_source_unlink_is_rolled_back(monkeypatch, tmp_path):
    source = tmp_path / "movie.mp4"
    temporary = tmp_path / ".movie.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    temporary.write_bytes(b"verified")
    real_unlink = filesystem.Path.unlink

    def cancel_source_unlink(path, *args, **kwargs):
        if path == source:
            raise CancellationRequested(15)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(filesystem.Path, "unlink", cancel_source_unlink)
    with pytest.raises(CancellationRequested):
        filesystem.install_verified(source, temporary, destination)

    assert source.read_bytes() == b"original"
    assert temporary.read_bytes() == b"verified"
    assert not destination.exists()


def test_atomic_write_failure_preserves_existing_destination(monkeypatch, tmp_path):
    destination = tmp_path / "metadata.nfo"
    destination.write_text("old")
    real_replace = os.replace

    def fail_replace(source, target):
        if target == destination:
            raise OSError("replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(filesystem.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        filesystem.atomic_write_text(destination, "new")

    assert destination.read_text() == "old"
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_write_preserves_existing_permissions(tmp_path):
    destination = tmp_path / "metadata.nfo"
    destination.write_text("old")
    destination.chmod(0o640)

    filesystem.atomic_write_text(destination, "new")

    assert destination.read_text() == "new"
    assert destination.stat().st_mode & 0o777 == 0o640
