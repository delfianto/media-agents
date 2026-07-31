from __future__ import annotations

import json
import os

from psammophis.runtime import journal as journal_mod
from psammophis.runtime.events import (
    EventEmitter,
    ItemCompleted,
    ItemProgress,
    ItemStarted,
    PhaseCompleted,
    PhaseStarted,
    RunCompleted,
    RunStarted,
)
from psammophis.runtime.journal import (
    JournalSink,
    annotate_stale,
    journal_paths,
    list_runs,
    read_events,
    read_status,
)


def test_journal_writes_status_events_summary(tmp_path):
    paths = journal_paths(tmp_path / "state", "run-abc")
    sink = JournalSink(paths, command="analyze", run_id="run-abc", pid=os.getpid())
    emitter = EventEmitter(sink, "run-abc", "analyze")
    emitter.emit(RunStarted, mode="dry-run", items_total=1, root=str(tmp_path))
    emitter.emit(RunCompleted, status="succeeded", exit_code=0, changed=0, errors=0)

    status = read_status(paths.status_path)
    assert status["run_id"] == "run-abc"
    assert status["state"] == "succeeded"
    assert status["exit_code"] == 0

    events = read_events(paths.events_path)
    assert len(events) == 2
    assert events[0]["event"] == "run.started"
    assert events[1]["event"] == "run.completed"

    summary = json.loads(paths.summary_path.read_text())
    assert summary["exit_code"] == 0

    listed = list_runs(tmp_path / "state")
    assert listed[0]["run_id"] == "run-abc"


def test_events_after_seq(tmp_path):
    paths = journal_paths(tmp_path / "state", "run-2")
    sink = JournalSink(paths, command="x", run_id="run-2", pid=os.getpid())
    emitter = EventEmitter(sink, "run-2", "x")
    emitter.emit(RunStarted)
    emitter.emit(RunCompleted, status="failed", exit_code=1, errors=1)
    assert len(read_events(paths.events_path, after=1)) == 1


def test_stale_detection_for_dead_pid(tmp_path):
    status = {
        "state": "running",
        "pid": 99999999,
        "hostname": __import__("socket").gethostname(),
    }
    annotated = annotate_stale(status)
    assert annotated["state"] == "stale"


def test_status_tracks_live_item_counts_and_clears_completed_phase(tmp_path):
    paths = journal_paths(tmp_path / "state", "run-live")
    sink = JournalSink(paths, command="x", run_id="run-live", pid=os.getpid())
    emitter = EventEmitter(sink, "run-live", "x")
    emitter.emit(RunStarted, items_total=1)
    emitter.emit(ItemStarted, item="movie.mkv", index=1, total=1, log_path="movie.log")
    emitter.emit(PhaseStarted, phase="encode", item="movie.mkv")
    emitter.emit(ItemProgress, item="movie.mkv", phase="encode", percent=25.0)
    emitter.emit(PhaseCompleted, phase="encode", item="movie.mkv", status="succeeded")
    emitter.emit(ItemCompleted, item="movie.mkv", status="succeeded")

    status = read_status(paths.status_path)
    assert status["items_total"] == 1
    assert status["raw_log_path"] == "movie.log"
    assert status["counts"]["items_succeeded"] == 1
    assert status["current_item"] is None
    assert status["current_phase"] is None


def test_stale_detection_rejects_reused_pid(monkeypatch):
    monkeypatch.setattr(journal_mod, "is_process_alive", lambda _pid: True)
    monkeypatch.setattr(journal_mod, "process_start_token", lambda _pid: "new-process")
    status = {
        "state": "running",
        "pid": os.getpid(),
        "hostname": __import__("socket").gethostname(),
        "process_start_token": "old-process",
    }
    annotated = annotate_stale(status)
    assert annotated["state"] == "stale"
    assert "reused" in annotated["stale_reason"]
