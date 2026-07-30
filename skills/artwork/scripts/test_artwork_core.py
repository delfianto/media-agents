from pathlib import Path

import pytest
from artwork.core import ArtworkError, MediaIdentity, build_plan, identify


class FakeTmdb:
    def movie_details(self, tmdb_id):
        return {
            "id": tmdb_id,
            "title": "Movie",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "credits": {},
        }

    def tv_details(self, tmdb_id):
        return {
            "id": tmdb_id,
            "name": "Show",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
        }

    def episode_details(self, tv_id, season, episode):
        return {
            "name": "Pilot",
            "still_path": "/still.jpg",
            "credits": {},
        }


def test_identify_movie_and_episode_from_provider_tags():
    movie = identify(Path("/Movies/Movie {tmdb-10}/Movie {tmdb-10}.mkv"))
    assert movie.tmdb_id == 10
    assert not movie.is_episode
    episode = identify(Path("/TV/Show [tmdbid-20]/Season 01/Show S01E02.mkv"))
    assert episode.tmdb_id == 20
    assert (episode.season, episode.episode) == (1, 2)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("poster", ["poster"]),
        ("fanart", ["fanart"]),
        ("nfo", ["nfo"]),
        ("all", ["poster", "fanart", "nfo"]),
    ],
)
def test_movie_artwork_type_selection(kind, expected):
    plan = build_plan(MediaIdentity(Path("/Movie/file.mkv"), 10), FakeTmdb(), kind)
    assert [item.kind for item in plan] == expected


def test_still_rejected_for_movie():
    with pytest.raises(ArtworkError):
        build_plan(MediaIdentity(Path("/Movie/file.mkv"), 10), FakeTmdb(), "still")
