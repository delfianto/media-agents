"""Typed progress events and the sink protocol.

Core code emits immutable events; presentation (TTY/plain/JSONL) and the
durable journal are separate consumers. Schema starts at 1.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

SCHEMA_VERSION = 1

RunStatus = Literal["running", "succeeded", "partial", "failed", "cancelled", "stale"]
MessageLevel = Literal["info", "warning", "error"]
ItemStatus = Literal["succeeded", "failed", "skipped", "cancelled"]
PhaseStatus = Literal["succeeded", "failed", "cancelled"]


def new_run_id() -> str:
    """Sortable unique run ID (time-based UUID string)."""
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Common envelope present on every serialized event."""

    event: str
    run_id: str
    seq: int
    timestamp: str
    command: str
    schema: int = SCHEMA_VERSION


def _omit_empty(data: dict[str, Any]) -> dict[str, Any]:
    """Drop None values; keep 0/False/empty containers only when meaningful.

    Optional event fields that are None are omitted rather than serialized
    as null or empty strings.
    """
    return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True, slots=True)
class Event:
    """Base event. Subclasses set ``event`` and optional payload fields."""

    run_id: str
    seq: int
    command: str
    timestamp: str = field(default_factory=utc_now)
    schema: int = SCHEMA_VERSION
    event: str = "event"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return _omit_empty(data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class RunStarted(Event):
    event: str = "run.started"
    root: str | None = None
    root_source: str | None = None
    mode: str | None = None  # dry-run | applied
    items_total: int | None = None
    reporter: str | None = None
    journal_path: str | None = None


@dataclass(frozen=True, slots=True)
class RunHeartbeat(Event):
    event: str = "run.heartbeat"
    phase: str | None = None
    item: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ItemStarted(Event):
    event: str = "item.started"
    item: str | None = None
    index: int | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class PhaseStarted(Event):
    event: str = "phase.started"
    phase: str | None = None
    item: str | None = None


@dataclass(frozen=True, slots=True)
class ItemProgress(Event):
    event: str = "item.progress"
    item: str | None = None
    phase: str | None = None
    percent: float | None = None
    media_position: float | None = None
    media_duration: float | None = None
    fps: float | None = None
    speed: float | None = None
    eta_seconds: float | None = None
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class Message(Event):
    event: str = "message"
    level: MessageLevel = "info"
    text: str = ""
    item: str | None = None
    phase: str | None = None


@dataclass(frozen=True, slots=True)
class PhaseCompleted(Event):
    event: str = "phase.completed"
    phase: str | None = None
    item: str | None = None
    status: PhaseStatus = "succeeded"
    elapsed_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ItemCompleted(Event):
    event: str = "item.completed"
    item: str | None = None
    status: ItemStatus = "succeeded"
    output: str | None = None
    detail: str | None = None
    log_path: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class RunCompleted(Event):
    event: str = "run.completed"
    status: RunStatus = "succeeded"
    exit_code: int = 0
    changed: int | None = None
    planned: int | None = None
    errors: int | None = None
    review: int | None = None
    elapsed_seconds: float | None = None
    journal_path: str | None = None


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...

    def close(self) -> None: ...


class NullSink:
    def emit(self, event: Event) -> None:
        return None

    def close(self) -> None:
        return None


class CompositeSink:
    def __init__(self, sinks: Sequence[EventSink]) -> None:
        self._sinks = list(sinks)

    def emit(self, event: Event) -> None:
        for sink in self._sinks:
            sink.emit(event)

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()


class SequenceCounter:
    """Strictly increasing sequence numbers starting at 1."""

    def __init__(self) -> None:
        self._seq = 0

    def next(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def current(self) -> int:
        return self._seq


class EventEmitter:
    """Helper that stamps run_id/seq/command onto typed events."""

    def __init__(self, sink: EventSink, run_id: str, command: str) -> None:
        self.sink = sink
        self.run_id = run_id
        self.command = command
        self._seq = SequenceCounter()

    def emit(self, event_cls: type[Event], **kwargs: Any) -> Event:
        event = event_cls(
            run_id=self.run_id,
            seq=self._seq.next(),
            command=self.command,
            **kwargs,
        )
        self.sink.emit(event)
        return event
