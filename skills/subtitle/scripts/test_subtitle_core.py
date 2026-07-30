from pathlib import Path

import pytest
from subtitle.core import SubtitleError, build_plans


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
