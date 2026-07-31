import pytest

from psammophis.organize import plan as plan_mod
from psammophis.organize.plan import Plan, execute_plan
from psammophis.runtime.signals import CancellationRequested


def test_overwrite_backs_up_existing_destination(tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "Movies" / "movie.mkv"
    destination.parent.mkdir()
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    result = execute_plan(
        Plan("movie", source, 1, destination),
        overwrite=True,
    )
    assert result.status == "moved"
    assert destination.read_bytes() == b"new"
    assert result.backup is not None
    assert result.backup.read_bytes() == b"old"


def test_existing_destination_is_refused_by_default(tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    result = execute_plan(Plan("movie", source, 1, destination))
    assert result.status == "error"
    assert source.exists()
    assert destination.read_bytes() == b"old"


def test_copy_mode_keeps_source_and_installs_complete_destination(tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "Movies" / "movie.mkv"
    source.write_bytes(b"new")

    result = execute_plan(Plan("movie", source, 1, destination), copy_instead_of_move=True)

    assert result.status == "moved"
    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"new"


def test_cancellation_before_source_removal_restores_existing_destination(monkeypatch, tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "Movies" / "movie.mkv"
    destination.parent.mkdir()
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    real_unlink = plan_mod.Path.unlink

    def cancel_source_unlink(path, *args, **kwargs):
        if path == source:
            raise CancellationRequested(15)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(plan_mod.Path, "unlink", cancel_source_unlink)
    with pytest.raises(CancellationRequested):
        execute_plan(Plan("movie", source, 1, destination), overwrite=True)

    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"
    assert not (destination.parent / "movie.mkv.bak").exists()


def test_source_and_destination_cannot_be_the_same_file(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original")

    result = execute_plan(Plan("movie", source, 1, source), overwrite=True)

    assert result.status == "error"
    assert "same file" in result.detail
    assert source.read_bytes() == b"original"


def test_symlink_destination_is_never_replaced_even_with_overwrite(tmp_path):
    source = tmp_path / "incoming.mkv"
    external = tmp_path / "external.mkv"
    destination = tmp_path / "Movies" / "movie.mkv"
    destination.parent.mkdir()
    source.write_bytes(b"new")
    external.write_bytes(b"external")
    destination.symlink_to(external)

    result = execute_plan(Plan("movie", source, 1, destination), overwrite=True)

    assert result.status == "error"
    assert "symlink" in result.detail
    assert source.read_bytes() == b"new"
    assert destination.is_symlink()
    assert external.read_bytes() == b"external"
