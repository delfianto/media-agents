from __future__ import annotations

from psammophis.abtest.report import format_report, score_from_compare, summarize_variants


def _compare(*, vmaf_mean: float, vmaf_min: float, s2: float) -> dict:
    return {
        "summary": {
            "vmaf": {
                "mean": vmaf_mean,
                "median": vmaf_mean,
                "p5": vmaf_mean - 1,
                "min": vmaf_min,
                "count": 480,
            },
            "float_ssim": {"mean": 0.99},
            "ssimulacra2": {"mean": s2, "min": s2 - 2},
        }
    }


def test_score_and_summary_order_and_size_ratio():
    a = score_from_compare(
        _compare(vmaf_mean=97.0, vmaf_min=94.0, s2=73.0),
        clip_id="c01",
        variant_id="current",
        why="x",
        start=0.0,
        enc_bytes=10_000_000,
        ref_bytes=80_000_000,
    )
    b = score_from_compare(
        _compare(vmaf_mean=98.2, vmaf_min=95.5, s2=75.0),
        clip_id="c01",
        variant_id="cand",
        why="x",
        start=0.0,
        enc_bytes=13_000_000,
        ref_bytes=80_000_000,
    )
    summaries = summarize_variants([a, b], {"current": "old", "cand": "new"})
    assert summaries[0].variant_id == "current"
    assert summaries[0].size_vs_first == 1.0
    assert summaries[1].size_vs_first == 1.3
    assert summaries[1].vmaf_mean == 98.2
    text = format_report("/src.mkv", [a, b], summaries)
    assert "ENCODER A/B" in text
    assert "current" in text
    assert "1.30x" in text
