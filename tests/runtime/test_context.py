from __future__ import annotations

from psammophis.runtime.context import AppContext


class CollectSink:
    def __init__(self) -> None:
        self.events = []
        self.closed = 0

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.closed += 1


def test_context_emits_exactly_one_terminal_and_reconciles_status():
    sink = CollectSink()
    context = AppContext(command="x", reporter="plain")
    context.set_reporter_sink(sink)
    context.start_run(command="x", mode="read-only")
    context.record_outcome(status="succeeded")
    context.complete_run(exit_code=1)
    context.complete_run(exit_code=0)

    terminals = [event for event in sink.events if event.event == "run.completed"]
    assert len(terminals) == 1
    assert terminals[0].status == "failed"
    assert terminals[0].exit_code == 1
    assert sink.closed == 1


def test_reporter_failure_is_best_effort():
    class BrokenSink:
        def emit(self, event) -> None:
            del event
            raise RuntimeError("render failed")

        def close(self) -> None:
            raise RuntimeError("close failed")

    context = AppContext(command="x")
    context.set_reporter_sink(BrokenSink())
    context.start_run(command="x")
    context.complete_run(exit_code=0)
    assert context.completed
