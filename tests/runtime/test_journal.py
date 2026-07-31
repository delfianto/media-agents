from __future__ import annotations

import json
import os

from psammophis.runtime.events import EventEmitter, RunCompleted, RunStarted
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
