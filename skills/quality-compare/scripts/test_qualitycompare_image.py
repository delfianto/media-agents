import pytest
from qualitycompare.image import _parse_metric


@pytest.mark.parametrize(
    ("metric", "output", "expected"),
    [
        ("SSIM", "29.3694 (0.000448149)", 0.999551851),
        ("SSIM", "0 (0)", 1.0),
        ("PSNR", "48.1234", 48.1234),
        ("RMSE", "123.4 (0.00188296)", 0.00188296),
    ],
)
def test_parse_imagemagick_metrics(metric, output, expected):
    assert _parse_metric(metric, output) == pytest.approx(expected)


def test_parse_metric_rejects_non_numeric_output():
    with pytest.raises(RuntimeError):
        _parse_metric("SSIM", "comparison failed")
