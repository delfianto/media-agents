from __future__ import annotations

from psammophis.runtime.outcome import BatchCounts, exit_for_errors


def test_batch_counts_exit_zero_without_errors():
    assert BatchCounts(changed=2, planned=1, review=3).exit_code() == 0


def test_batch_counts_exit_one_with_errors():
    assert BatchCounts(changed=1, errors=1).exit_code() == 1


def test_exit_for_errors():
    assert exit_for_errors(0) == 0
    assert exit_for_errors(3) == 1
