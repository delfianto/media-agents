"""Signal-to-exception bridge used by the foreground application runtime."""

from __future__ import annotations


class CancellationRequested(BaseException):
    """A terminating OS signal requested controlled cancellation.

    This deliberately follows ``KeyboardInterrupt`` rather than ordinary
    operational exceptions. Per-item ``except Exception`` handlers must not
    turn SIGTERM into a recoverable item error and continue mutating the next
    file in a batch.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(f"cancellation requested by signal {signum}")
        self.signum = signum
