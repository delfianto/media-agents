from __future__ import annotations

import pytest

from psammophis.compare.cli import _positive, _validate_json_destination


def test_json_output_cannot_overwrite_either_media_input(tmp_path):
    reference = (tmp_path / "reference.mkv").resolve()
    distorted = (tmp_path / "distorted.mkv").resolve()

    with pytest.raises(ValueError, match="must not overwrite"):
        _validate_json_destination(reference, reference, distorted)
    with pytest.raises(ValueError, match="must not overwrite"):
        _validate_json_destination(distorted, reference, distorted)


def test_separate_json_output_is_allowed(tmp_path):
    reference = (tmp_path / "reference.mkv").resolve()
    distorted = (tmp_path / "distorted.mkv").resolve()
    _validate_json_destination(tmp_path / "report.json", reference, distorted)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_positive_work_values_reject_nonfinite_and_nonpositive(value):
    with pytest.raises(ValueError):
        _positive("value", value)
