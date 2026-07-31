from __future__ import annotations

import json
import signal
import sys

from psammophis import cli
from psammophis.runtime.signals import CancellationRequested


def test_handler_type_error_is_not_retried(monkeypatch, capsys):
    calls = 0

    def handler(_args, _context):
        nonlocal calls
        calls += 1
        raise TypeError("raised inside handler")

    monkeypatch.setattr(cli, "_load_handler", lambda _command: handler)
    code = cli.main(["--reporter", "quiet", "env-check"])

    assert code == 1
    assert calls == 1
    assert "raised inside handler" in capsys.readouterr().err


def test_handler_import_failure_still_has_structured_terminal(monkeypatch, capsys):
    def fail_import(_command):
        raise ImportError("feature dependency is broken")

    monkeypatch.setattr(cli, "_load_handler", fail_import)
    assert cli.main(["--reporter", "jsonl", "env-check"]) == 1

    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [event["event"] for event in events] == [
        "run.started",
        "message",
        "run.completed",
    ]
    assert "feature dependency is broken" in events[1]["text"]
    assert events[-1]["status"] == "failed"


def test_negative_progress_interval_is_rejected(capsys):
    assert cli.main(["--progress-interval", "-1", "env-check"]) == 2
    assert "must be a finite number zero or greater" in capsys.readouterr().err


def test_journal_initialization_failure_has_structured_terminal(monkeypatch, tmp_path, capsys):
    state_file = tmp_path / "not-a-directory"
    state_file.write_text("occupied")

    def handler(_args, context):
        context.start_run(command="env-check", mode="read-only", wants_journal=True)
        return 0

    monkeypatch.setattr(cli, "_load_handler", lambda _command: handler)
    code = cli.main(
        [
            "--reporter",
            "jsonl",
            "--journal",
            "--state-dir",
            str(state_file),
            "env-check",
        ]
    )

    assert code == 1
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events[0]["event"] == "run.started"
    assert any(
        event.get("event") == "message" and "Journal disabled" in event.get("text", "")
        for event in events
    )
    assert events[-1]["event"] == "run.completed"
    assert events[-1]["status"] == "failed"


def test_renamed_features_accept_only_their_final_public_names(monkeypatch, capsys):
    loaded: list[tuple[str, str]] = []

    def load(command):
        def handler(_args, context):
            loaded.append((command, context.command))
            return 0

        return handler

    monkeypatch.setattr(cli, "_load_handler", load)

    assert cli.main(["--reporter", "quiet", "transcode", "list-presets"]) == 0
    assert cli.main(["--reporter", "quiet", "compare", "--help"]) == 0
    assert loaded == [
        ("transcode", "transcode"),
        ("compare", "compare"),
    ]

    assert cli.main(["av1-transcode"]) == 2
    assert cli.main(["quality-compare"]) == 2
    errors = capsys.readouterr().err
    assert "unknown command 'av1-transcode'" in errors
    assert "unknown command 'quality-compare'" in errors


def test_jsonl_stderr_is_structured_and_preserves_early_order(monkeypatch, capsys):
    def handler(_args, context):
        print("early warning", file=sys.stderr)
        context.start_run(command="env-check", mode="read-only")
        print("later error", file=sys.stderr)
        return 0

    monkeypatch.setattr(cli, "_load_handler", lambda _command: handler)
    assert cli.main(["--reporter", "jsonl", "env-check"]) == 0

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.err.splitlines() if line]
    assert [event["event"] for event in lines] == [
        "run.started",
        "message",
        "message",
        "run.completed",
    ]
    assert [event.get("text") for event in lines[1:3]] == ["early warning", "later error"]
    assert lines[-1]["status"] == "succeeded"
    assert captured.out == ""


def test_applied_handler_writes_complete_journal(monkeypatch, tmp_path):
    def handler(_args, context):
        context.start_run(
            command="track-strip apply",
            root=tmp_path,
            mode="applied",
            wants_journal=True,
        )
        context.record_outcome(status="succeeded", changed=1, errors=0)
        return 0

    monkeypatch.setattr(cli, "_load_handler", lambda _command: handler)
    state = tmp_path / "state"
    assert (
        cli.main(
            [
                "--reporter",
                "quiet",
                "--state-dir",
                str(state),
                "track-strip",
                "apply",
            ]
        )
        == 0
    )

    run_dirs = list((state / "runs").iterdir())
    assert len(run_dirs) == 1
    events = [json.loads(line) for line in (run_dirs[0] / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["run.started", "run.completed"]
    assert events[-1]["changed"] == 1
    assert json.loads((run_dirs[0] / "summary.json").read_text())["status"] == "succeeded"


def test_sigterm_maps_to_cancelled_terminal_event(monkeypatch, capsys):
    def handler(_args, context):
        context.start_run(command="env-check", mode="read-only")
        raise CancellationRequested(signal.SIGTERM)

    monkeypatch.setattr(cli, "_load_handler", lambda _command: handler)
    assert cli.main(["--reporter", "jsonl", "env-check"]) == 143
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    terminals = [event for event in events if event["event"] == "run.completed"]
    assert len(terminals) == 1
    assert terminals[0]["status"] == "cancelled"
    assert terminals[0]["exit_code"] == 143
