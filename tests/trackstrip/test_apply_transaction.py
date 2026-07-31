from __future__ import annotations

from psammophis.runtime.signals import CancellationRequested
from psammophis.trackstrip import apply as apply_mod


class FakeBackend:
    def __init__(self) -> None:
        self.remux_calls = 0

    def build_command(self, _source, _output, _plan):
        return ["fake-remux"]

    def remux(self, _source, output, _plan, on_heartbeat=None):
        del on_heartbeat
        self.remux_calls += 1
        output.write_text("verified")


def _execute(monkeypatch, tmp_path, backend, *, backup_dir=None, on_phase=None):
    source = tmp_path / "movie.mkv"
    if not source.exists():
        source.write_text("original")
    monkeypatch.setattr(apply_mod, "probe_file", lambda _path: {"streams": [], "format": {}})
    monkeypatch.setattr(apply_mod, "verify_output", lambda _original, _new: (True, "ok"))
    result, _plan = apply_mod._execute_backend_plan(
        source,
        tmp_path,
        backend,
        {"changed": True},
        backup_dir,
        True,
        ".preview",
        on_phase,
    )
    return source, result


def test_existing_recovery_temp_is_preserved(monkeypatch, tmp_path):
    backend = FakeBackend()
    source = tmp_path / "movie.mkv"
    source.write_text("original")
    temporary = tmp_path / ".movie.trackstrip-tmp.mkv"
    temporary.write_text("recovery output")

    source, result = _execute(monkeypatch, tmp_path, backend)

    assert result.status == "error"
    assert "temporary work file already exists" in result.detail
    assert source.read_text() == "original"
    assert temporary.read_text() == "recovery output"
    assert backend.remux_calls == 0


def test_backup_collision_is_rejected_before_remux(monkeypatch, tmp_path):
    backend = FakeBackend()
    backup_root = tmp_path / "backups"
    backup = backup_root / "movie.mkv"
    backup.parent.mkdir()
    backup.write_text("older original")

    source, result = _execute(monkeypatch, tmp_path, backend, backup_dir=backup_root)

    assert result.status == "error"
    assert "backup already exists" in result.detail
    assert source.read_text() == "original"
    assert backup.read_text() == "older original"
    assert backend.remux_calls == 0


def test_phase_failure_before_commit_restores_original(monkeypatch, tmp_path):
    backend = FakeBackend()
    backup_root = tmp_path / "backups"

    def phase(name, state):
        if name == "commit" and state == "started":
            raise RuntimeError("journal failed")

    source, result = _execute(
        monkeypatch,
        tmp_path,
        backend,
        backup_dir=backup_root,
        on_phase=phase,
    )

    assert result.status == "error"
    assert source.read_text() == "original"
    assert not (backup_root / "movie.mkv").exists()
    assert not (tmp_path / ".movie.trackstrip-tmp.mkv").exists()


def test_cancellation_cleans_temp_and_never_becomes_item_error(monkeypatch, tmp_path):
    class CancellingBackend(FakeBackend):
        def remux(self, _source, output, _plan, on_heartbeat=None):
            del on_heartbeat
            output.write_text("partial")
            raise CancellationRequested(15)

    backend = CancellingBackend()
    source = tmp_path / "movie.mkv"
    source.write_text("original")
    monkeypatch.setattr(apply_mod, "probe_file", lambda _path: {"streams": [], "format": {}})

    try:
        apply_mod._execute_backend_plan(
            source,
            tmp_path,
            backend,
            {"changed": True},
            None,
            True,
            ".preview",
        )
    except CancellationRequested:
        pass
    else:
        raise AssertionError("cancellation was converted into an ordinary item result")

    assert source.read_text() == "original"
    assert not (tmp_path / ".movie.trackstrip-tmp.mkv").exists()
