import pytest

from psammophis.compare.sampling import clip_ranges, stratified_timestamps


def test_stratified_timestamps_are_centered_in_equal_buckets():
    assert stratified_timestamps(100, 4) == [12.5, 37.5, 62.5, 87.5]


def test_stratified_timestamps_respect_margin():
    assert stratified_timestamps(100, 2, margin=10) == [30, 70]


def test_clip_ranges_stay_inside_duration():
    ranges = clip_ranges(100, 4, 10)
    assert ranges == [(11.25, 10), (33.75, 10), (56.25, 10), (78.75, 10)]
    assert all(start >= 0 and start + duration <= 100 for start, duration in ranges)


def test_clip_range_uses_whole_video_when_clip_is_longer():
    assert clip_ranges(8, 12, 10) == [(0, 8)]


def test_invalid_duration_is_rejected():
    with pytest.raises(ValueError):
        stratified_timestamps(0, 4)
