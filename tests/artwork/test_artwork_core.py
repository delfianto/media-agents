from pathlib import Path

import pytest

from psammophis.artwork.core import ArtworkError, MediaIdentity, build_plan, identify


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

    def season_details(self, tv_id, season):
        return {"poster_path": "/season-poster.jpg"}


def test_identify_movie_and_episode_from_provider_tags():
    movie = identify(Path("/Movies/Movie {tmdb-10}/Movie {tmdb-10}.mkv"))
    assert movie.tmdb_id == 10
    assert not movie.is_episode
    episode = identify(Path("/TV/Show [tmdbid-20]/Season 01/Show S01E02.mkv"))
    assert episode.tmdb_id == 20
    assert (episode.season, episode.episode) == (1, 2)


def test_identify_falls_back_to_nfo_uniqueid_when_no_path_tag(tmp_path):
    video = tmp_path / "Movie (2020)" / "Movie (2020).mkv"
    video.parent.mkdir()
    video.write_bytes(b"")
    nfo_path = video.with_suffix(".nfo")
    nfo_path.write_text(
        '<movie><title>Movie</title><uniqueid type="tmdb" default="true">555</uniqueid></movie>',
        encoding="utf-8",
    )
    identity = identify(video)
    assert identity.tmdb_id == 555
    assert not identity.is_episode


def test_identify_nfo_fallback_ignores_non_tmdb_uniqueid(tmp_path):
    video = tmp_path / "Movie (2020)" / "Movie (2020).mkv"
    video.parent.mkdir()
    video.write_bytes(b"")
    nfo_path = video.with_suffix(".nfo")
    nfo_path.write_text(
        '<movie><uniqueid type="imdb">tt1234567</uniqueid></movie>', encoding="utf-8"
    )
    with pytest.raises(ArtworkError):
        identify(video)


def test_identify_raises_when_no_tag_and_no_nfo(tmp_path):
    video = tmp_path / "Movie (2020)" / "Movie (2020).mkv"
    video.parent.mkdir()
    video.write_bytes(b"")
    with pytest.raises(ArtworkError):
        identify(video)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("poster", ["poster"]),
        ("fanart", ["fanart"]),
        ("cover", ["cover"]),
        ("nfo", ["nfo"]),
        ("all", ["poster", "fanart", "cover", "nfo"]),
    ],
)
def test_movie_artwork_type_selection(kind, expected):
    plan = build_plan(MediaIdentity(Path("/Movie/file.mkv"), 10), FakeTmdb(), kind)
    assert [item.kind for item in plan] == expected


def test_still_rejected_for_movie():
    with pytest.raises(ArtworkError):
        build_plan(MediaIdentity(Path("/Movie/file.mkv"), 10), FakeTmdb(), "still")


def test_movie_cover_embeds_poster_into_the_movie_file():
    video = Path("/Movie/file.mkv")
    plan = build_plan(MediaIdentity(video, 10), FakeTmdb(), "cover")
    write = plan[0]
    assert write.kind == "cover"
    assert write.image_path == "/poster.jpg"
    assert write.embed_cover_into == video
    assert write.destination == video.parent / ".movie-poster.jpg"


def _episode_identity():
    video = Path("/TV/Show (2020)/Season 01/Show - S01E01 - Pilot.mkv")
    return MediaIdentity(video, 10, season=1, episode=1)


def test_episode_cover_embeds_season_poster_into_the_episode_file():
    identity = _episode_identity()
    plan = build_plan(identity, FakeTmdb(), "cover")
    assert [item.kind for item in plan] == ["cover"]
    write = plan[0]
    assert write.image_path == "/season-poster.jpg"
    assert write.embed_cover_into == identity.video
    assert write.destination == identity.video.parent / ".season-poster.jpg"


def test_episode_cover_falls_back_to_series_poster_when_season_has_none():
    class NoSeasonPosterTmdb(FakeTmdb):
        def season_details(self, tv_id, season):
            return {}

    plan = build_plan(_episode_identity(), NoSeasonPosterTmdb(), "cover")
    assert plan[0].image_path == "/poster.jpg"


def test_episode_all_includes_cover():
    plan = build_plan(_episode_identity(), FakeTmdb(), "all")
    assert "cover" in [item.kind for item in plan]


def test_episode_all_excludes_still():
    """The season-poster cover replaces the per-episode still in the default
    bundle -- no need to also fetch/store a screencap. `--type still` stays
    available standalone for anyone who explicitly wants one."""
    plan = build_plan(_episode_identity(), FakeTmdb(), "all")
    assert "still" not in [item.kind for item in plan]


def test_episode_still_type_still_available_standalone():
    plan = build_plan(_episode_identity(), FakeTmdb(), "still")
    assert [item.kind for item in plan] == ["still"]
