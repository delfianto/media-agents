from __future__ import annotations

import json
import os

from psammophis.runtime.events import EventEmitter, RunCompleted, RunStarted
from psammophis.runtime.journal import JournalSink, journal_paths
from psammophis.runtime.runs_cli import main as runs_main


def test_runs_list_show_events(tmp_path, capsys):
    state = tmp_path / "state"
    paths = journal_paths(state, "rid-1")
    sink = JournalSink(paths, command="env-check", run_id="rid-1", pid=os.getpid())
    emitter = EventEmitter(sink, "rid-1", "env-check")
    emitter.emit(RunStarted, mode="dry-run")
    emitter.emit(RunCompleted, status="succeeded", exit_code=0)

    assert runs_main(["--state-dir", str(state), "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["run_id"] == "rid-1"

    assert runs_main(["--state-dir", str(state), "show", "rid-1"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["state"] == "succeeded"

    assert runs_main(["--state-dir", str(state), "events", "rid-1", "--after", "0"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert [e["event"] for e in lines] == ["run.started", "run.completed"]


def test_runs_unknown(tmp_path):
    assert runs_main(["--state-dir", str(tmp_path), "show", "missing"]) == 1


def test_runs_rejects_path_traversal(tmp_path, capsys):
    assert runs_main(["--state-dir", str(tmp_path), "show", "../outside"]) == 2
    assert "invalid run ID" in capsys.readouterr().err
