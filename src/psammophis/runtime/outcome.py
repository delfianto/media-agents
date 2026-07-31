"""Shared command outcome / exit-code helpers.

Exit codes:

- 0 success (including dry-run with no operational errors)
- 1 operational failure or partial batch failure
- 2 CLI usage / configuration error (usually argparse SystemExit)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BatchCounts:
    """Aggregate counters for a batch command."""

    changed: int = 0
    planned: int = 0
    unchanged: int = 0
    review: int = 0
    errors: int = 0
    extra: dict[str, int] = field(default_factory=dict)

    def exit_code(self) -> int:
        return 1 if self.errors else 0


def exit_for_errors(errors: int) -> int:
    return 1 if errors else 0
