"""Progress reporters: plain, JSONL, quiet, TTY (Rich), and auto selection."""

from __future__ import annotations

import sys
import time
from typing import TextIO

from .events import Event, EventSink


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
            print(f"[progress] {item} {phase} {pct}", file=self.stream, flush=True)
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
            print(
                f"[run] {data.get('status')} exit={data.get('exit_code')} "
                f"changed={data.get('changed')} errors={data.get('errors')}",
                file=self.stream,
                flush=True,
            )
            return
        if name in ("item.started", "item.completed", "phase.started", "phase.completed"):
            detail = data.get("item") or data.get("phase") or ""
            print(f"[{name}] {detail}", file=self.stream, flush=True)

    def close(self) -> None:
        return None


class JsonlReporter:
    """One JSON object per line on stderr, flushed immediately."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stderr if stream is None else stream

    def emit(self, event: Event) -> None:
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
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
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
            total = data.get("items_total") or 0
            self._overall_id = self._progress.add_task(
                f"{data.get('command')}", total=float(total) if total else None
            )
            return
        if name == "item.started":
            self._ensure_started()
            if self._item_id is not None:
                self._progress.remove_task(self._item_id)
            item = str(data.get("item") or "")
            desc = item if len(item) < 60 else "…" + item[-57:]
            self._item_id = self._progress.add_task(desc, total=100.0)
            return
        if name == "item.progress":
            if self._item_id is not None and data.get("percent") is not None:
                self._progress.update(self._item_id, completed=float(data["percent"]))
            return
        if name == "phase.started":
            if self._item_id is not None:
                phase = data.get("phase") or ""
                item = data.get("item") or ""
                short = item if len(str(item)) < 40 else "…" + str(item)[-37:]
                self._progress.update(self._item_id, description=f"{phase}: {short}")
            return
        if name == "item.completed":
            if self._item_id is not None:
                self._progress.update(self._item_id, completed=100.0)
            if self._overall_id is not None:
                self._progress.advance(self._overall_id, 1)
            status = data.get("status")
            item = data.get("item") or ""
            self.console.print(f"  [{status}] {item} {data.get('detail') or ''}")
            return
        if name == "message":
            level = str(data.get("level", "info"))
            style = {"warning": "yellow", "error": "red"}.get(level, "white")
            self.console.print(f"[{style}]{level.upper()}: {data.get('text', '')}[/]")
            return
        if name == "run.completed":
            self.close()
            self.console.print(
                f"[run] {data.get('status')} exit={data.get('exit_code')} "
                f"changed={data.get('changed')} errors={data.get('errors')}"
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
        return JsonlReporter(stream)
    if name == "quiet":
        return QuietReporter(stream)
    if name == "plain":
        return PlainReporter(stream, progress_interval=progress_interval)
    if name == "tty":
        if not getattr(stream, "isatty", lambda: False)():
            return PlainReporter(stream, progress_interval=progress_interval)
        return TtyReporter(stream)
    # auto
    if getattr(stream, "isatty", lambda: False)():
        return TtyReporter(stream)
    return PlainReporter(stream, progress_interval=progress_interval)
