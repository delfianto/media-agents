"""Kodi-compatible NFO XML builders (movie.nfo / tvshow.nfo / episode nfo) --
the local-metadata format both Plex and Jellyfin read, letting a file carry
its own already-resolved TMDB/IMDb identity instead of leaning on either
server's own online lookup at scan time. Pure string building: takes an
already-fetched metadata dataclass, returns XML text. No I/O, no network.

Tag set and structure verified against Jellyfin's own NFO-parsing docs
(organize/../../notes/library-naming.md has the full citation) -- notably the
`<uniqueid type="tmdb" default="true">` / `<uniqueid type="imdb">` shape,
which is what lets a file be identified without any title/year guessing at
all once this NFO exists next to it.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.dom import minidom


@dataclass(frozen=True)
class Actor:
    name: str
    role: str | None = None
    order: int | None = None


@dataclass(frozen=True)
class MovieNfoData:
    title: str
    tmdb_id: int
    original_title: str | None = None
    year: int | None = None
    plot: str | None = None
    tagline: str | None = None
    runtime_minutes: int | None = None
    mpaa: str | None = None
    premiered: str | None = None  # YYYY-MM-DD
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    imdb_id: str | None = None


@dataclass(frozen=True)
class ShowNfoData:
    title: str
    tmdb_id: int
    plot: str | None = None
    premiered: str | None = None
    mpaa: str | None = None
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    imdb_id: str | None = None


@dataclass(frozen=True)
class EpisodeNfoData:
    title: str
    show_title: str
    season: int
    episode: int
    plot: str | None = None
    aired: str | None = None  # YYYY-MM-DD
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    tmdb_id: int | None = None


def _add_text(parent: ET.Element, tag: str, value: object) -> None:
    if value is None or value == "":
        return
    el = ET.SubElement(parent, tag)
    el.text = str(value)


def _add_repeated(parent: ET.Element, tag: str, values: list[str]) -> None:
    for value in values:
        _add_text(parent, tag, value)


def _add_actors(parent: ET.Element, actors: list[Actor]) -> None:
    for actor in actors:
        actor_el = ET.SubElement(parent, "actor")
        _add_text(actor_el, "name", actor.name)
        _add_text(actor_el, "role", actor.role)
        _add_text(actor_el, "order", actor.order)


def _add_unique_ids(parent: ET.Element, tmdb_id: int | None, imdb_id: str | None) -> None:
    if tmdb_id is not None:
        el = ET.SubElement(parent, "uniqueid", type="tmdb", default="true")
        el.text = str(tmdb_id)
    if imdb_id:
        el = ET.SubElement(parent, "uniqueid", type="imdb")
        el.text = imdb_id


def _prettify(root: ET.Element) -> str:
    rough = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    # minidom emits its own XML declaration without our preferred encoding
    # attribute and adds a stray blank line after it -- normalize both.
    lines = [line for line in pretty.splitlines() if line.strip()]
    lines[0] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    return "\n".join(lines) + "\n"


def build_movie_nfo(data: MovieNfoData) -> str:
    root = ET.Element("movie")
    _add_text(root, "title", data.title)
    _add_text(root, "originaltitle", data.original_title)
    _add_text(root, "year", data.year)
    _add_text(root, "plot", data.plot)
    _add_text(root, "tagline", data.tagline)
    _add_text(root, "runtime", data.runtime_minutes)
    _add_text(root, "mpaa", data.mpaa)
    _add_text(root, "premiered", data.premiered)
    _add_repeated(root, "genre", data.genres)
    _add_repeated(root, "studio", data.studios)
    _add_repeated(root, "director", data.directors)
    _add_repeated(root, "credits", data.writers)
    _add_actors(root, data.actors)
    _add_unique_ids(root, data.tmdb_id, data.imdb_id)
    return _prettify(root)


def build_tvshow_nfo(data: ShowNfoData) -> str:
    root = ET.Element("tvshow")
    _add_text(root, "title", data.title)
    _add_text(root, "plot", data.plot)
    _add_text(root, "premiered", data.premiered)
    _add_text(root, "mpaa", data.mpaa)
    _add_repeated(root, "genre", data.genres)
    _add_repeated(root, "studio", data.studios)
    _add_actors(root, data.actors)
    _add_unique_ids(root, data.tmdb_id, data.imdb_id)
    return _prettify(root)


def build_episode_nfo(data: EpisodeNfoData) -> str:
    root = ET.Element("episodedetails")
    _add_text(root, "title", data.title)
    _add_text(root, "showtitle", data.show_title)
    _add_text(root, "season", data.season)
    _add_text(root, "episode", data.episode)
    _add_text(root, "plot", data.plot)
    _add_text(root, "aired", data.aired)
    _add_repeated(root, "director", data.directors)
    _add_repeated(root, "credits", data.writers)
    _add_actors(root, data.actors)
    if data.tmdb_id is not None:
        _add_unique_ids(root, data.tmdb_id, None)
    return _prettify(root)
