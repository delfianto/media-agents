"""Application context passed from the top-level dispatcher into features."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .events import CompositeSink, EventEmitter, EventSink, NullSink, new_run_id
from .journal import JournalSink, default_state_root, journal_paths

ReporterName = Literal["auto", "tty", "plain", "jsonl", "quiet"]


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
    emitter: EventEmitter | None = None
    journal_paths: object | None = None

    def attach_emitter(self, command: str, sink: EventSink | None = None) -> EventEmitter:
        self.command = command
        if sink is not None:
            self.sink = sink
        self.emitter = EventEmitter(self.sink, self.run_id, command)
        return self.emitter


def build_sink(
    context: AppContext,
    *,
    command: str,
    wants_journal: bool,
    media_root: Path | None = None,
    reporter_sink: EventSink | None = None,
) -> tuple[EventSink, object | None]:
    """Compose reporter + optional journal sinks.

    Journal policy: enabled when ``context.journal is True``, disabled when
    False, otherwise when ``wants_journal`` is True and a state root exists.
    """
    sinks: list[EventSink] = []
    if reporter_sink is not None:
        sinks.append(reporter_sink)

    enable_journal = context.journal if context.journal is not None else wants_journal
    paths = None
    if enable_journal:
        state_root = default_state_root(
            state_dir=context.state_dir,
            media_root=media_root,
            medialib_root=__import__("os").environ.get("MEDIALIB_ROOT"),
        )
        if state_root is not None:
            paths = journal_paths(state_root, context.run_id)
            sinks.append(JournalSink(paths, command=command, run_id=context.run_id))

    if not sinks:
        return NullSink(), None
    if len(sinks) == 1:
        return sinks[0], paths
    return CompositeSink(sinks), paths
