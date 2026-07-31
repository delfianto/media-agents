import pytest
from qualitycompare.report import percentile, summarize


def test_percentile_interpolates():
    assert percentile([0, 10, 20], 25) == 5
    assert percentile([20, 0, 10], 50) == 10


def test_summarize_reports_lower_tail_and_mean():
    result = summarize([70, 80, 90, 100])
    assert result["count"] == 4
    assert result["min"] == 70
    assert result["median"] == 85
    assert result["mean"] == 85
    assert result["p5"] == pytest.approx(71.5)
