from __future__ import annotations

import pytest
from medialib.humansize import human_size


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (None, "0 B"),
        (0, "0 B"),
        (500, "500.0 B"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**4, "1.0 TB"),
        (1024**5, "1.0 PB"),
    ],
)
def test_human_size(n, expected):
    assert human_size(n) == expected
