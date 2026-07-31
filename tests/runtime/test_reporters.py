from __future__ import annotations

import io
import json

from psammophis.runtime import reporters as reporters_mod
from psammophis.runtime.events import (
    EventEmitter,
    ItemCompleted,
    ItemProgress,
    ItemStarted,
    Message,
    PhaseStarted,
    RunCompleted,
    RunHeartbeat,
    RunStarted,
)
from psammophis.runtime.reporters import (
    JsonlReporter,
    PlainReporter,
    QuietReporter,
    TtyReporter,
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


def test_plain_progress_includes_metrics_and_heartbeat():
    buf = io.StringIO()
    sink = PlainReporter(buf, progress_interval=0.0)
    emitter = EventEmitter(sink, "r1", "x")
    emitter.emit(
        ItemProgress,
        item="a.mkv",
        phase="encode",
        percent=25.0,
        fps=12.5,
        speed=0.5,
        eta_seconds=90,
        backend="ffmpeg",
    )
    emitter.emit(RunHeartbeat, item="a.mkv", phase="encode")
    out = buf.getvalue()
    assert "12.5fps" in out
    assert "0.50x" in out
    assert "eta=00:01:30" in out
    assert "[heartbeat] a.mkv" in out


def test_jsonl_progress_throttles_small_deltas(monkeypatch):
    times = iter((100.0, 101.0, 102.0, 103.0))
    monkeypatch.setattr(reporters_mod.time, "monotonic", lambda: next(times))
    buf = io.StringIO()
    sink = JsonlReporter(buf, progress_interval=10.0)
    emitter = EventEmitter(sink, "r1", "x")
    emitter.emit(ItemProgress, item="a", percent=10.0)
    emitter.emit(ItemProgress, item="a", percent=10.2)
    emitter.emit(ItemProgress, item="a", percent=11.0)
    emitter.emit(ItemProgress, item="a", percent=100.0)
    values = [json.loads(line)["percent"] for line in buf.getvalue().splitlines()]
    assert values == [10.0, 11.0, 100.0]


def test_plain_phase_line_includes_phase_and_item():
    buf = io.StringIO()
    emitter = EventEmitter(PlainReporter(buf), "r1", "x")
    emitter.emit(PhaseStarted, phase="verify", item="movie.mkv")
    assert "verify — movie.mkv" in buf.getvalue()


def test_tty_treats_media_names_as_text_not_rich_markup():
    buf = io.StringIO()
    sink = TtyReporter(buf)
    emitter = EventEmitter(sink, "r1", "x")
    emitter.emit(RunStarted, items_total=1)
    emitter.emit(ItemStarted, item="[bold]movie[/].mkv", index=1, total=1)
    emitter.emit(Message, level="error", text="bad [link=file:///tmp]name[/link]")
    emitter.emit(ItemCompleted, item="[bold]movie[/].mkv", status="failed")
    emitter.emit(RunCompleted, status="failed", exit_code=1, errors=1)
    assert "[bold]movie[/].mkv" in buf.getvalue()
