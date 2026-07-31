"""Application context passed from the top-level dispatcher into features."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from .events import (
    CompositeSink,
    Event,
    EventEmitter,
    EventSink,
    GuardedSink,
    Message,
    NullSink,
    RunCompleted,
    RunStarted,
    new_run_id,
)
from .journal import JournalError, JournalPaths, JournalSink, default_state_root, journal_paths

ReporterName = Literal["auto", "tty", "plain", "jsonl", "quiet"]


class JournalConfigurationError(ValueError):
    """Journaling was explicitly requested but no state location is available."""


@dataclass(slots=True)
class AppContext:
    """Runtime options shared by every command invocation."""

    run_id: str = field(default_factory=new_run_id)
    reporter: ReporterName = "auto"
    progress_interval: float = 10.0
    state_dir: Path | None = None
    journal: bool | None = None  # None = policy default
    command: str = ""
    sink: EventSink = field(default_factory=NullSink)
    reporter_sink: EventSink = field(default_factory=NullSink)
    emitter: EventEmitter | None = None
    journal_paths: JournalPaths | None = None
    started: bool = False
    completed: bool = False
    _started_monotonic: float | None = None
    outcome_status: Literal["succeeded", "partial", "failed", "cancelled"] | None = None
    outcome_changed: int | None = None
    outcome_planned: int | None = None
    outcome_errors: int | None = None
    outcome_review: int | None = None

    def attach_emitter(self, command: str, sink: EventSink | None = None) -> EventEmitter:
        self.command = command
        if sink is not None:
            self.sink = sink
        if self.emitter is None or self.emitter.command != command:
            self.emitter = EventEmitter(self.sink, self.run_id, command)
        else:
            self.emitter.replace_sink(self.sink)
        return self.emitter

    def set_reporter_sink(self, sink: EventSink) -> None:
        guarded = GuardedSink(sink)
        self.reporter_sink = guarded
        self.sink = guarded
        if self.emitter is not None:
            self.emitter.replace_sink(guarded)

    def disable_journal(self) -> None:
        """Detach a failed journal while preserving the live reporter."""
        self.journal = False
        self.sink = self.reporter_sink
        if self.emitter is not None:
            self.emitter.replace_sink(self.reporter_sink)

    def start_run(
        self,
        *,
        command: str | None = None,
        root: Path | str | None = None,
        root_source: str | None = None,
        mode: str | None = None,
        items_total: int | None = None,
        wants_journal: bool = False,
        use_root_for_state: bool = True,
    ) -> EventEmitter:
        """Configure sinks and emit the sole run-start event."""
        if self.started:
            assert self.emitter is not None
            return self.emitter

        selected_command = command or self.command
        media_root = Path(root) if root is not None and use_root_for_state else None
        sink, paths = build_sink(
            self,
            command=selected_command,
            wants_journal=wants_journal,
            media_root=media_root,
            reporter_sink=self.reporter_sink,
        )
        self.sink = sink
        self.journal_paths = paths
        emitter = self.attach_emitter(selected_command, sink)
        self._started_monotonic = monotonic()
        self.started = True
        started_fields = {
            "root": str(root) if root is not None else None,
            "root_source": root_source,
            "mode": mode,
            "items_total": items_total,
            "reporter": self.reporter,
            "journal_path": str(paths.run_dir) if paths is not None else None,
        }
        try:
            emitter.emit(RunStarted, **started_fields)
        except JournalError:
            self.disable_journal()
            emitter.emit(RunStarted, **started_fields)
            raise
        return emitter

    def emit(self, event_cls: type[Event], **kwargs: object) -> Event:
        if not self.started:
            self.start_run()
        assert self.emitter is not None
        return self.emitter.emit(event_cls, **kwargs)

    def message(
        self,
        text: str,
        *,
        level: Literal["info", "warning", "error"] = "info",
        item: str | None = None,
        phase: str | None = None,
    ) -> None:
        self.emit(Message, level=level, text=text, item=item, phase=phase)

    def complete_run(
        self,
        *,
        exit_code: int,
        status: Literal["succeeded", "partial", "failed", "cancelled"] | None = None,
        changed: int | None = None,
        planned: int | None = None,
        errors: int | None = None,
        review: int | None = None,
    ) -> None:
        """Emit exactly one authoritative terminal event and close consumers."""
        if self.completed:
            return
        if not self.started:
            self.start_run()
        if status is None:
            if exit_code == 0:
                status = "succeeded"
            elif exit_code == 1 and self.outcome_status == "partial":
                status = "partial"
            else:
                status = "failed"
        changed = self.outcome_changed if changed is None else changed
        planned = self.outcome_planned if planned is None else planned
        errors = self.outcome_errors if errors is None else errors
        review = self.outcome_review if review is None else review
        elapsed = (
            monotonic() - self._started_monotonic if self._started_monotonic is not None else None
        )
        assert self.emitter is not None
        self.emitter.emit(
            RunCompleted,
            status=status,
            exit_code=exit_code,
            changed=changed,
            planned=planned,
            errors=errors,
            review=review,
            elapsed_seconds=elapsed,
            journal_path=(
                str(self.journal_paths.run_dir) if self.journal_paths is not None else None
            ),
        )
        self.completed = True
        self.sink.close()

    def record_outcome(
        self,
        *,
        status: Literal["succeeded", "partial", "failed", "cancelled"] | None = None,
        changed: int | None = None,
        planned: int | None = None,
        errors: int | None = None,
        review: int | None = None,
    ) -> None:
        self.outcome_status = status
        self.outcome_changed = changed
        self.outcome_planned = planned
        self.outcome_errors = errors
        self.outcome_review = review


def build_sink(
    context: AppContext,
    *,
    command: str,
    wants_journal: bool,
    media_root: Path | None = None,
    reporter_sink: EventSink | None = None,
) -> tuple[EventSink, JournalPaths | None]:
    """Compose reporter + optional journal sinks.

    Journal policy: enabled when ``context.journal is True``, disabled when
    False, otherwise when ``wants_journal`` is True and a state root exists.
    """
    sinks: list[EventSink] = []

    enable_journal = context.journal if context.journal is not None else wants_journal
    paths = None
    if enable_journal:
        state_root = default_state_root(
            state_dir=context.state_dir,
            media_root=media_root,
            medialib_root=__import__("os").environ.get("MEDIALIB_ROOT"),
        )
        if state_root is None:
            if context.journal is True:
                raise JournalConfigurationError(
                    "journaling was requested but no state location is available; "
                    "pass --state-dir, set MEDIALIB_ROOT, or use a root-oriented command"
                )
        else:
            paths = journal_paths(state_root, context.run_id)
            sinks.append(JournalSink(paths, command=command, run_id=context.run_id))

    if reporter_sink is not None:
        sinks.append(reporter_sink)

    if not sinks:
        return NullSink(), None
    if len(sinks) == 1:
        return sinks[0], paths
    return CompositeSink(sinks), paths
