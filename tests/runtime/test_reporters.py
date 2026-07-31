from __future__ import annotations

import io
import json

from psammophis.runtime.events import (
    EventEmitter,
    ItemProgress,
    Message,
    RunCompleted,
    RunStarted,
)
from psammophis.runtime.reporters import (
    JsonlReporter,
    PlainReporter,
    QuietReporter,
    select_reporter,
)


def test_jsonl_one_object_per_line():
    buf = io.StringIO()
    sink = JsonlReporter(buf)
    emitter = EventEmitter(sink, "r1", "analyze")
    emitter.emit(RunStarted, mode="dry-run")
    emitter.emit(RunCompleted, status="succeeded", exit_code=0)
    lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        data = json.loads(line)
        assert "event" in data
        assert data["run_id"] == "r1"
    assert "\x1b" not in buf.getvalue()
    assert "\r" not in buf.getvalue()


def test_plain_no_ansi():
    buf = io.StringIO()
    sink = PlainReporter(buf, progress_interval=0.0)
    emitter = EventEmitter(sink, "r1", "x")
    emitter.emit(RunStarted)
    emitter.emit(ItemProgress, item="a.mkv", phase="encode", percent=10.0)
    emitter.emit(Message, level="warning", text="heads up")
    out = buf.getvalue()
    assert "\x1b" not in out
    assert "[WARNING]" in out


def test_quiet_hides_progress_keeps_errors():
    buf = io.StringIO()
    sink = QuietReporter(buf)
    emitter = EventEmitter(sink, "r1", "x")
    emitter.emit(ItemProgress, item="a", percent=50.0)
    emitter.emit(Message, level="error", text="boom")
    out = buf.getvalue()
    assert "progress" not in out.lower() or "boom" in out
    assert "boom" in out


def test_auto_non_tty_is_plain():
    buf = io.StringIO()
    sink = select_reporter("auto", stderr=buf)
    assert isinstance(sink, PlainReporter)
