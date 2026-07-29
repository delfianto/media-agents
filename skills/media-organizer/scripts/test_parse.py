from __future__ import annotations

from mediaorganizer import parse


def test_parse_movie_with_year_and_release_tags():
    result = parse.parse("The.Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-GROUP.mkv")
    assert result is not None
    assert result.kind == "movie"
    assert result.title == "The Matrix"
    assert result.year == 1999
    assert result.season is None
    assert result.episode is None


def test_parse_episode_with_title():
    result = parse.parse("Breaking.Bad.S01E01.Pilot.720p.BluRay.x264-GROUP.mkv")
    assert result is not None
    assert result.kind == "episode"
    assert result.title == "Breaking Bad"
    assert result.season == 1
    assert result.episode == 1
    assert result.episode_title == "Pilot"


def test_parse_anime_release_group_brackets():
    result = parse.parse("[SubsPlease] Spy x Family - 01 (1080p) [ABCDEF12].mkv")
    assert result is not None
    assert result.kind == "episode"
    assert result.title == "Spy x Family"
    assert result.episode == 1


def test_parse_multi_episode_file_takes_first_episode():
    result = parse.parse("Some.Show.2023.S02E05-E06.1080p.WEB-DL.mkv")
    assert result is not None
    assert result.kind == "episode"
    assert result.season == 2
    assert result.episode == 5


def test_parse_extracts_embedded_tmdb_id():
    result = parse.parse("Movie Name (2020) {tmdb-12345}.mkv")
    assert result is not None
    assert result.kind == "movie"
    assert result.tmdb_id == 12345


def test_parse_unparseable_name_returns_none():
    result = parse.parse("asdf.mkv")
    assert result is None or result.title
