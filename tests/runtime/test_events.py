from __future__ import annotations

import json
import threading

from psammophis.runtime.events import (
    SCHEMA_VERSION,
    CompositeSink,
    EventEmitter,
    ItemProgress,
    Message,
    NullSink,
    RunCompleted,
    RunStarted,
    SequenceCounter,
    new_run_id,
)


class CollectSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None:
        return None


def test_sequence_counter_monotonic():
    counter = SequenceCounter()
    assert counter.next() == 1
    assert counter.next() == 2
    assert counter.current == 2


def test_run_id_unique():
    assert new_run_id() != new_run_id()


def test_run_ids_sort_in_creation_order():
    run_ids = [new_run_id() for _ in range(20)]
    assert run_ids == sorted(run_ids)


def test_serialization_omits_none_and_has_envelope():
    event = RunStarted(
        run_id="r1",
        seq=1,
        command="analyze",
        root="/media",
        root_source="MEDIALIB_ROOT",
        mode="dry-run",
        items_total=None,
        reporter="jsonl",
        journal_path=None,
    )
    data = event.to_dict()
    assert data["schema"] == SCHEMA_VERSION
    assert data["event"] == "run.started"
    assert data["run_id"] == "r1"
    assert data["seq"] == 1
    assert "items_total" not in data
    assert "journal_path" not in data
    assert "root" in data
    # Valid JSON
    json.loads(event.to_json())


def test_emitter_stamps_seq():
    sink = CollectSink()
    emitter = EventEmitter(sink, "run-1", "transcode run")
    emitter.emit(RunStarted, mode="applied", items_total=2)
    emitter.emit(ItemProgress, item="a.mkv", percent=10.0)
    emitter.emit(RunCompleted, status="succeeded", exit_code=0, changed=2, errors=0)
    assert [e.seq for e in sink.events] == [1, 2, 3]
    assert sink.events[-1].event == "run.completed"
    assert isinstance(sink.events[-1], RunCompleted)


def test_composite_and_null():
    a, b = CollectSink(), CollectSink()
    composite = CompositeSink([a, b])
    event = RunStarted(run_id="r", seq=1, command="x")
    composite.emit(event)
    assert a.events == [event]
    assert b.events == [event]
    NullSink().emit(event)


def test_emitter_serializes_events_from_multiple_threads():
    sink = CollectSink()
    emitter = EventEmitter(sink, "run-threaded", "x")

    def emit_batch(worker: int) -> None:
        for index in range(20):
            emitter.emit(Message, text=f"{worker}:{index}")

    threads = [threading.Thread(target=emit_batch, args=(worker,)) for worker in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [event.seq for event in sink.events] == list(range(1, 101))
