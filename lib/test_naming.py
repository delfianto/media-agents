from __future__ import annotations

from pathlib import Path

import pytest
from medialib import naming


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Matrix", "The Matrix"),
        ("Se7en", "Se7en"),
        ('Title: With <bad> "chars"/\\|?*', "Title With bad chars"),
        ("  extra   spaces  ", "extra spaces"),
    ],
)
def test_sanitize_title(title, expected):
    assert naming.sanitize_title(title) == expected


def test_movie_paths_plex():
    meta = naming.MovieMeta(title="The Matrix", year=1999, tmdb_id=603)
    folder = naming.movie_folder(meta, "plex", "/Movies")
    video = naming.movie_video_path(meta, "plex", "/Movies", ".mkv")
    assert folder == Path("/Movies/The Matrix (1999) {tmdb-603}")
    assert video == folder / "The Matrix (1999) {tmdb-603}.mkv"


def test_movie_paths_jellyfin():
    meta = naming.MovieMeta(title="The Matrix", year=1999, tmdb_id=603)
    folder = naming.movie_folder(meta, "jellyfin", "/Movies")
    video = naming.movie_video_path(meta, "jellyfin", "/Movies", ".mkv")
    assert folder == Path("/Movies/The Matrix (1999) [tmdbid-603]")
    assert video == folder / "The Matrix (1999) [tmdbid-603].mkv"


def test_movie_paths_unknown_server_raises():
    meta = naming.MovieMeta(title="X", year=2000, tmdb_id=1)
    with pytest.raises(ValueError):
        naming.movie_folder(meta, "emby", "/Movies")


def test_movie_paths_no_year():
    meta = naming.MovieMeta(title="Untitled", year=None, tmdb_id=42)
    folder = naming.movie_folder(meta, "plex", "/Movies")
    assert folder == Path("/Movies/Untitled {tmdb-42}")


def test_episode_paths_plex():
    meta = naming.EpisodeMeta(
        series_title="Breaking Bad",
        series_year=2008,
        series_tmdb_id=1396,
        season=1,
        episode=1,
        episode_title="Pilot",
    )
    series = naming.series_folder(meta, "plex", "/TV")
    season = naming.season_folder(meta, "plex", "/TV")
    video = naming.episode_video_path(meta, "plex", "/TV", ".mkv")
    assert series == Path("/TV/Breaking Bad (2008) {tmdb-1396}")
    assert season == series / "Season 01"
    assert video == season / "Breaking Bad - s01e01 - Pilot.mkv"


def test_episode_paths_jellyfin():
    meta = naming.EpisodeMeta(
        series_title="Breaking Bad",
        series_year=2008,
        series_tmdb_id=1396,
        season=1,
        episode=1,
        episode_title="Pilot",
    )
    series = naming.series_folder(meta, "jellyfin", "/TV")
    video = naming.episode_video_path(meta, "jellyfin", "/TV", ".mkv")
    assert series == Path("/TV/Breaking Bad (2008) [tmdbid-1396]")
    assert video == series / "Season 01" / "Breaking Bad (2008) S01E01 Pilot.mkv"


def test_episode_paths_no_episode_title():
    meta = naming.EpisodeMeta(
        series_title="Show", series_year=2020, series_tmdb_id=1, season=2, episode=13
    )
    video = naming.episode_video_path(meta, "plex", "/TV", ".mkv")
    assert video.name == "Show - s02e13.mkv"


def test_episode_season_padding_double_digit():
    meta = naming.EpisodeMeta(
        series_title="Show", series_year=2020, series_tmdb_id=1, season=12, episode=3
    )
    season = naming.season_folder(meta, "plex", "/TV")
    assert season.name == "Season 12"


def test_sidecar_and_artwork_paths():
    video = Path("/Movies/Foo (2020) {tmdb-1}/Foo (2020) {tmdb-1}.mkv")
    assert naming.sidecar_path(video, ".nfo") == video.with_name("Foo (2020) {tmdb-1}.nfo")
    assert naming.subtitle_path(video, "en") == video.with_name("Foo (2020) {tmdb-1}.en.srt")
    assert naming.poster_path(video.parent) == video.parent / "poster.jpg"
    assert naming.fanart_path(video.parent) == video.parent / "fanart.jpg"
    assert naming.tvshow_nfo_path(video.parent) == video.parent / "tvshow.nfo"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Movie (2020) {tmdb-12345}.mkv", 12345),
        ("Movie (2020) [tmdbid-987].mkv", 987),
        ("Movie (2020).mkv", None),
        ("Movie {tmdb-}.mkv", None),
        ("Movie [tmdbid-abc].mkv", None),
    ],
)
def test_extract_provider_id(name, expected):
    assert naming.extract_provider_id(name) == expected
