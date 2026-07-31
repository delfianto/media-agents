"""Progress reporters: plain, JSONL, quiet, TTY (Rich), and auto selection."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, TextIO

from .events import Event, EventSink

if TYPE_CHECKING:
    from .context import AppContext


class PlainReporter:
    """Newline-delimited human text on stderr, no ANSI."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        progress_interval: float = 10.0,
    ) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.progress_interval = progress_interval
        self._last_progress_at = 0.0
        self._last_percent: float | None = None

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        name = data.get("event")
        if name == "item.started":
            self._last_progress_at = 0.0
            self._last_percent = None
        if name == "item.progress":
            now = time.monotonic()
            percent = data.get("percent")
            pct_changed = percent is not None and (
                self._last_percent is None or abs(float(percent) - self._last_percent) >= 1.0
            )
            if not pct_changed and now - self._last_progress_at < self.progress_interval:
                return
            self._last_progress_at = now
            if percent is not None:
                self._last_percent = float(percent)
            item = data.get("item") or ""
            phase = data.get("phase") or ""
            pct = f"{percent:.1f}%" if percent is not None else "?"
            metrics: list[str] = []
            if data.get("fps") is not None:
                metrics.append(f"{float(data['fps']):.1f}fps")
            if data.get("speed") is not None:
                metrics.append(f"{float(data['speed']):.2f}x")
            if data.get("eta_seconds") is not None:
                metrics.append(f"eta={_format_duration(float(data['eta_seconds']))}")
            if data.get("backend"):
                metrics.append(str(data["backend"]))
            suffix = f" {' '.join(metrics)}" if metrics else ""
            print(f"[progress] {item} {phase} {pct}{suffix}", file=self.stream, flush=True)
            return
        if name == "message":
            level = str(data.get("level", "info")).upper()
            print(f"[{level}] {data.get('text', '')}", file=self.stream, flush=True)
            return
        if name == "run.started":
            print(
                f"[run] started {data.get('command')} id={data.get('run_id')}",
                file=self.stream,
                flush=True,
            )
            return
        if name == "run.completed":
            counts = " ".join(
                f"{key}={data[key]}"
                for key in ("changed", "planned", "errors", "review")
                if data.get(key) is not None
            )
            suffix = f" {counts}" if counts else ""
            print(
                f"[run] {data.get('status')} exit={data.get('exit_code')}{suffix}",
                file=self.stream,
                flush=True,
            )
            return
        if name == "run.heartbeat":
            detail = data.get("item") or data.get("phase") or data.get("message") or ""
            print(f"[heartbeat] {detail}", file=self.stream, flush=True)
            return
        if name == "item.completed":
            suffix = data.get("detail") or data.get("output") or ""
            print(
                f"[item] {data.get('status')} {data.get('item') or ''} {suffix}".rstrip(),
                file=self.stream,
                flush=True,
            )
            return
        if name in ("item.started", "phase.started", "phase.completed"):
            if name == "item.started":
                detail = data.get("item") or ""
            else:
                phase = data.get("phase") or ""
                item = data.get("item")
                detail = f"{phase} — {item}" if item else phase
            status = f" {data.get('status')}" if data.get("status") else ""
            print(f"[{name}]{status} {detail}", file=self.stream, flush=True)

    def close(self) -> None:
        return None


class JsonlReporter:
    """One JSON object per line on stderr, flushed immediately."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        progress_interval: float = 10.0,
    ) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.progress_interval = progress_interval
        self._last_progress_at = 0.0
        self._last_percent: float | None = None
        self._last_item: str | None = None

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        if data.get("event") == "item.progress":
            item = str(data.get("item") or "")
            if item != self._last_item:
                self._last_item = item
                self._last_percent = None
                self._last_progress_at = 0.0
            now = time.monotonic()
            percent = data.get("percent")
            pct_changed = percent is not None and (
                self._last_percent is None or abs(float(percent) - self._last_percent) >= 1.0
            )
            is_terminal_progress = percent is not None and float(percent) >= 100.0
            if (
                not pct_changed
                and not is_terminal_progress
                and now - self._last_progress_at < self.progress_interval
            ):
                return
            self._last_progress_at = now
            if percent is not None:
                self._last_percent = float(percent)
        print(event.to_json(), file=self.stream, flush=True)

    def close(self) -> None:
        return None


class QuietReporter:
    """Suppress routine progress; keep warnings/errors and non-success terminals."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stderr if stream is None else stream

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        name = data.get("event")
        if name == "message" and data.get("level") in ("warning", "error"):
            print(
                f"[{str(data['level']).upper()}] {data.get('text', '')}",
                file=self.stream,
                flush=True,
            )
        elif name == "run.completed" and data.get("status") not in ("succeeded",):
            print(
                f"[run] {data.get('status')} exit={data.get('exit_code')}",
                file=self.stream,
                flush=True,
            )

    def close(self) -> None:
        return None


class TtyReporter:
    """Rich live display on stderr for interactive terminals."""

    def __init__(self, stream: TextIO | None = None) -> None:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TaskID,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        self.stream = sys.stderr if stream is None else stream
        self.console = Console(file=self.stream, stderr=True, highlight=False)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", style="bold blue", markup=False),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[metrics]}", justify="right"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            transient=False,
        )
        self._overall_id: TaskID | None = None
        self._item_id: TaskID | None = None
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self._progress.start()
            self._started = True

    def emit(self, event: Event) -> None:
        data = event.to_dict()
        name = data.get("event")
        if name == "run.started":
            self._ensure_started()
            total = data.get("items_total")
            self._overall_id = self._progress.add_task(
                f"{data.get('command')}",
                total=float(total) if total is not None else None,
                metrics="",
            )
            return
        if name == "item.started":
            self._ensure_started()
            if self._overall_id is not None and data.get("total") is not None:
                self._progress.update(self._overall_id, total=float(data["total"]))
            if self._item_id is not None:
                self._progress.remove_task(self._item_id)
            item = str(data.get("item") or "")
            desc = item if len(item) < 60 else "…" + item[-57:]
            self._item_id = self._progress.add_task(desc, total=None, metrics="")
            return
        if name == "item.progress":
            if self._item_id is not None:
                metrics: list[str] = []
                if data.get("fps") is not None:
                    metrics.append(f"{float(data['fps']):.1f}fps")
                if data.get("speed") is not None:
                    metrics.append(f"{float(data['speed']):.2f}x")
                if data.get("backend"):
                    metrics.append(str(data["backend"]))
                if data.get("eta_seconds") is not None:
                    metrics.append(f"eta={_format_duration(float(data['eta_seconds']))}")
                if data.get("percent") is not None:
                    self._progress.update(
                        self._item_id,
                        total=100.0,
                        completed=float(data["percent"]),
                        metrics=" ".join(metrics),
                    )
                else:
                    self._progress.update(self._item_id, metrics=" ".join(metrics))
            return
        if name == "phase.started":
            if self._item_id is not None:
                phase = data.get("phase") or ""
                item = data.get("item") or ""
                short = item if len(str(item)) < 40 else "…" + str(item)[-37:]
                self._progress.update(
                    self._item_id,
                    description=f"{phase}: {short}",
                    total=None,
                    completed=0.0,
                )
            return
        if name == "item.completed":
            if self._item_id is not None:
                self._progress.update(self._item_id, total=100.0, completed=100.0)
            if self._overall_id is not None:
                self._progress.advance(self._overall_id, 1)
            status = data.get("status")
            item = data.get("item") or ""
            self.console.print(
                f"  [{status}] {item} {data.get('detail') or ''}",
                markup=False,
            )
            return
        if name == "run.heartbeat":
            if self._item_id is not None:
                phase = data.get("phase") or "working"
                self._progress.update(self._item_id, description=f"{phase}: still running")
            return
        if name == "message":
            level = str(data.get("level", "info"))
            style = {"warning": "yellow", "error": "red"}.get(level, "white")
            self.console.print(
                f"{level.upper()}: {data.get('text', '')}",
                style=style,
                markup=False,
            )
            return
        if name == "run.completed":
            self.close()
            counts = " ".join(
                f"{key}={data[key]}"
                for key in ("changed", "planned", "errors", "review")
                if data.get(key) is not None
            )
            suffix = f" {counts}" if counts else ""
            self.console.print(
                f"[run] {data.get('status')} exit={data.get('exit_code')}{suffix}",
                markup=False,
            )

    def close(self) -> None:
        if self._started:
            self._progress.stop()
            self._started = False


def select_reporter(
    name: str,
    *,
    progress_interval: float = 10.0,
    stderr: TextIO | None = None,
) -> EventSink:
    stream = sys.stderr if stderr is None else stderr
    if name == "jsonl":
        return JsonlReporter(stream, progress_interval=progress_interval)
    if name == "quiet":
        return QuietReporter(stream)
    if name == "plain":
        return PlainReporter(stream, progress_interval=progress_interval)
    if name == "tty":
        if not getattr(stream, "isatty", lambda: False)():
            print(
                "[WARNING] --reporter tty requested without an interactive stderr; using plain",
                file=stream,
                flush=True,
            )
            return PlainReporter(stream, progress_interval=progress_interval)
        return TtyReporter(stream)
    # auto
    if getattr(stream, "isatty", lambda: False)():
        return TtyReporter(stream)
    return PlainReporter(stream, progress_interval=progress_interval)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class JsonlStderrBridge:
    """Turn unstructured feature stderr lines into JSONL message events."""

    def __init__(self, context: AppContext) -> None:
        self.context = context
        self._buffer = ""
        self._pending: list[str] = []

    def write(self, value: str) -> int:
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._accept(line.rstrip("\r"))
        return len(value)

    def flush(self) -> None:
        return None

    def finish(self) -> None:
        if self._buffer:
            self._accept(self._buffer.rstrip("\r"))
            self._buffer = ""
        if not self.context.started:
            self.context.start_run()
        self._flush_pending()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"

    def _accept(self, line: str) -> None:
        if not line:
            return
        if self.context.started:
            self._flush_pending()
            self._emit(line)
        else:
            self._pending.append(line)

    def _flush_pending(self) -> None:
        for line in self._pending:
            self._emit(line)
        self._pending.clear()

    def _emit(self, line: str) -> None:
        upper = line.upper()
        if "ERROR" in upper or "FAILED" in upper or upper.startswith("TRACEBACK"):
            level = "error"
        elif "WARNING" in upper or "WARN" in upper:
            level = "warning"
        else:
            level = "info"
        try:
            self.context.message(line, level=level)
        except Exception as exc:
            from .journal import JournalError

            if not isinstance(exc, JournalError):
                raise
            self.context.disable_journal()
            self.context.message(f"Journal disabled after failure: {exc}", level="error")
            self.context.message(line, level=level)
