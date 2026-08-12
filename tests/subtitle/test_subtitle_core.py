from pathlib import Path

import pytest

from psammophis.subtitle.core import SubtitleError, build_plans


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
