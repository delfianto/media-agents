from pathlib import Path

import pytest

from psammophis.subtitle.core import SubtitleError, build_plans, select_result


def test_movie_plan_reads_file_provider_tag():
    video = Path("/Movies/Movie {tmdb-10}/Movie {tmdb-10}.mkv")
    plans = build_plans(video, ("en", "id"))
    assert [plan.tmdb_id for plan in plans] == [10, 10]
    assert [plan.destination.name for plan in plans] == [
        "Movie {tmdb-10}.en.srt",
        "Movie {tmdb-10}.id.srt",
    ]


def test_episode_plan_reads_series_tag_and_episode_number():
    video = Path("/TV/Show [tmdbid-20]/Season 02/Show S02E03.mkv")
    plan = build_plans(video, ("en",))[0]
    assert plan.tmdb_id == 20
    assert (plan.season, plan.episode) == (2, 3)


def test_missing_provider_tag_is_rejected():
    with pytest.raises(SubtitleError):
        build_plans(Path("/TV/Show/Season 01/Show S01E01.mkv"), ("en",))


def test_falls_back_to_nfo_uniqueid_when_no_path_tag(tmp_path):
    video = tmp_path / "Movie (2020)" / "Movie (2020).mkv"
    video.parent.mkdir()
    video.write_bytes(b"")
    video.with_suffix(".nfo").write_text(
        '<movie><uniqueid type="tmdb" default="true">555</uniqueid></movie>',
        encoding="utf-8",
    )
    plans = build_plans(video, ("en",))
    assert plans[0].tmdb_id == 555


def _result(file_id, fps=0.0, download_count=0, season=None, episode=None):
    attributes = {
        "fps": fps,
        "download_count": download_count,
        "files": [{"file_id": file_id}],
    }
    if season is not None or episode is not None:
        attributes["feature_details"] = {"season_number": season, "episode_number": episode}
    return {"attributes": attributes}


def test_select_result_prefers_matching_fps_over_higher_download_count():
    # Real case that motivated this: American Gods S01E01 -- result[0] was a
    # 29.97fps DVDRip with fewer downloads than several 23.976fps releases
    # that actually matched the source; picking result[0] blindly produced a
    # subtitle that silently ran out ~3.5 minutes before the episode ended.
    wrong_fps_but_popular = _result(1, fps=29.97, download_count=999999)
    right_fps = _result(2, fps=23.976, download_count=100)
    best = select_result([wrong_fps_but_popular, right_fps], target_fps=23.976)
    assert best is right_fps


def test_select_result_breaks_ties_by_download_count_within_fps_match():
    low = _result(1, fps=23.976, download_count=100)
    high = _result(2, fps=23.976, download_count=200000)
    best = select_result([low, high], target_fps=23.976)
    assert best is high


def test_select_result_falls_back_to_download_count_when_no_fps_matches():
    a = _result(1, fps=25.0, download_count=50)
    b = _result(2, fps=29.97, download_count=500)
    best = select_result([a, b], target_fps=23.976)
    assert best is b


def test_select_result_falls_back_to_download_count_when_target_fps_unknown():
    a = _result(1, fps=25.0, download_count=50)
    b = _result(2, fps=29.97, download_count=500)
    best = select_result([a, b], target_fps=None)
    assert best is b


def test_select_result_ignores_results_with_no_files():
    empty = {"attributes": {"fps": 23.976, "download_count": 999, "files": []}}
    real = _result(1, fps=23.976, download_count=1)
    assert select_result([empty, real], target_fps=23.976) is real


def test_select_result_empty_list_returns_none():
    assert select_result([], target_fps=23.976) is None


def test_select_result_rejects_wrong_episode_even_with_higher_download_count():
    # Real case found live-testing tmdb_id=46639 (American Gods) S01E01:
    # the search endpoint's season/episode filter isn't airtight -- a
    # mislabeled-filename result slipped through. This covers the sharper
    # version of that risk: a result whose own structured feature_details
    # say it's a *different* episode entirely, which would be wrong
    # content, not just wrong timing.
    wrong_episode_but_popular = _result(1, fps=23.976, download_count=999999, season=1, episode=3)
    right_episode = _result(2, fps=23.976, download_count=100, season=1, episode=1)
    best = select_result(
        [wrong_episode_but_popular, right_episode], target_fps=23.976, season=1, episode=1
    )
    assert best is right_episode


def test_select_result_treats_missing_feature_details_as_a_match():
    # Most results don't carry feature_details at all -- season/episode
    # filtering shouldn't shrink the pool to nothing just because a result
    # didn't report it.
    no_details = _result(1, fps=23.976, download_count=50)
    best = select_result([no_details], target_fps=23.976, season=1, episode=1)
    assert best is no_details


def test_select_result_falls_back_when_no_episode_matches():
    only_wrong_episode = _result(1, fps=23.976, download_count=50, season=1, episode=3)
    best = select_result([only_wrong_episode], target_fps=23.976, season=1, episode=1)
    assert best is only_wrong_episode


def test_select_result_ignores_episode_filter_for_movies():
    a = _result(1, fps=23.976, download_count=50)
    best = select_result([a], target_fps=23.976, season=None, episode=None)
    assert best is a
