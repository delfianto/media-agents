from __future__ import annotations

import xml.etree.ElementTree as ET

from psammophis.artwork import nfo


def test_movie_nfo_is_well_formed_and_round_trips_core_fields():
    data = nfo.MovieNfoData(
        title="The Matrix",
        tmdb_id=603,
        original_title="The Matrix",
        year=1999,
        plot="A hacker learns the truth.",
        genres=["Action", "Science Fiction"],
        directors=["Lana Wachowski", "Lilly Wachowski"],
        actors=[nfo.Actor(name="Keanu Reeves", role="Neo", order=0)],
        imdb_id="tt0133093",
    )
    xml_text = nfo.build_movie_nfo(data)
    root = ET.fromstring(xml_text)

    assert root.tag == "movie"
    assert root.findtext("title") == "The Matrix"
    assert root.findtext("year") == "1999"
    assert [g.text for g in root.findall("genre")] == ["Action", "Science Fiction"]
    assert [d.text for d in root.findall("director")] == [
        "Lana Wachowski",
        "Lilly Wachowski",
    ]

    actor = root.find("actor")
    assert actor is not None
    assert actor.findtext("name") == "Keanu Reeves"
    assert actor.findtext("role") == "Neo"

    unique_ids = root.findall("uniqueid")
    tmdb_uid = next(u for u in unique_ids if u.get("type") == "tmdb")
    imdb_uid = next(u for u in unique_ids if u.get("type") == "imdb")
    assert tmdb_uid.text == "603"
    assert tmdb_uid.get("default") == "true"
    assert imdb_uid.text == "tt0133093"


def test_movie_nfo_omits_absent_optional_fields():
    data = nfo.MovieNfoData(title="Untitled", tmdb_id=1)
    root = ET.fromstring(nfo.build_movie_nfo(data))
    assert root.find("tagline") is None
    assert root.find("mpaa") is None
    assert root.find("genre") is None
    assert root.find("actor") is None
    # tmdb uniqueid is still always present -- it's the whole point of the file
    assert root.find("uniqueid") is not None


def test_movie_nfo_without_imdb_id_has_only_tmdb_uniqueid():
    data = nfo.MovieNfoData(title="X", tmdb_id=42)
    root = ET.fromstring(nfo.build_movie_nfo(data))
    unique_ids = root.findall("uniqueid")
    assert len(unique_ids) == 1
    assert unique_ids[0].get("type") == "tmdb"


def test_tvshow_nfo_structure():
    data = nfo.ShowNfoData(title="Breaking Bad", tmdb_id=1396, genres=["Drama"])
    root = ET.fromstring(nfo.build_tvshow_nfo(data))
    assert root.tag == "tvshow"
    assert root.findtext("title") == "Breaking Bad"
    assert root.findtext("genre") == "Drama"


def test_episode_nfo_structure():
    data = nfo.EpisodeNfoData(
        title="Pilot",
        show_title="Breaking Bad",
        season=1,
        episode=1,
        aired="2008-01-20",
        tmdb_id=62085,
    )
    root = ET.fromstring(nfo.build_episode_nfo(data))
    assert root.tag == "episodedetails"
    assert root.findtext("title") == "Pilot"
    assert root.findtext("showtitle") == "Breaking Bad"
    assert root.findtext("season") == "1"
    assert root.findtext("episode") == "1"
    assert root.findtext("aired") == "2008-01-20"
    assert root.find("uniqueid") is not None


def test_xml_declaration_present_and_utf8():
    data = nfo.MovieNfoData(title="Amélie", tmdb_id=194, plot="Une jeune femme à Montmartre.")
    xml_text = nfo.build_movie_nfo(data)
    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"')
    root = ET.fromstring(xml_text)
    assert root.findtext("title") == "Amélie"
