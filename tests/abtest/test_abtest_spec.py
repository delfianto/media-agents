from __future__ import annotations

import pytest

from psammophis.abtest.spec import parse_starts, parse_variant, plan_windows, resolve_variant
from psammophis.medialib.svt import SvtImplementation


def test_parse_variant_named_preset():
    variant = parse_variant("current=2160p-film")
    assert variant.id == "current"
    assert variant.base_name == "2160p-film"
    assert variant.crf is None


def test_parse_variant_overrides():
    variant = parse_variant("cand=2160p-film,crf=26,grain=0,denoise=0")
    assert variant.id == "cand"
    assert variant.base_name == "2160p-film"
    assert variant.crf == 26
    assert variant.grain == 0
    assert variant.denoise is False


def test_parse_variant_raw_keys():
    variant = parse_variant("t5=crf=30,preset=2,tune=5,grain=0")
    assert variant.base_name is None
    assert variant.crf == 30
    assert variant.svt_preset == 2
    assert variant.tune == 5


def test_parse_variant_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown variant key"):
        parse_variant("x=2160p-film,cq=20")


def test_parse_starts():
    assert parse_starts("55, 620.5") == [55.0, 620.5]


def test_plan_windows_explicit_and_clamped():
    windows = plan_windows(900.0, starts=[55.0, 890.0], clip_count=6, clip_duration=20.0)
    assert len(windows) == 2
    assert windows[0].start == 55.0
    assert windows[0].duration == 20.0
    assert windows[1].duration == pytest.approx(10.0)


def test_plan_windows_rejects_start_past_duration():
    with pytest.raises(ValueError, match="past source"):
        plan_windows(100.0, starts=[200.0], clip_count=1, clip_duration=20.0)


def test_plan_windows_stratified_count():
    windows = plan_windows(600.0, starts=None, clip_count=4, clip_duration=20.0)
    assert len(windows) == 4
    assert all(item.duration == 20.0 for item in windows)


def test_resolve_variant_uses_table_hdr_crf():
    impl = SvtImplementation("svt-av1-hdr", "v4.1.0", "test")
    resolved = resolve_variant(parse_variant("cur=2160p-film"), 2160, "film", impl)
    assert resolved.crf == 26
    assert resolved.grain == 0
    assert resolved.denoise is False
    assert resolved.svt_preset == 5
    assert resolved.tune == 0


def test_resolve_variant_override_crf():
    impl = SvtImplementation("svt-av1-hdr", "v4.1.0", "test")
    resolved = resolve_variant(
        parse_variant("cand=2160p-film,crf=24,preset=2"),
        2160,
        "film",
        impl,
    )
    assert resolved.crf == 24
    assert resolved.svt_preset == 2
