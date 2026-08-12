"""Destination path/filename builders for Plex and Jellyfin's naming
conventions. Pure string/path formatting -- no I/O, no network calls.

Conventions (see ../../notes/library-naming.md for the full citations):
  - Movie/series folders and movie files: plain `Title (Year)` -- no
    `{tmdb-N}`/`[tmdbid-N]` provider-ID tag. Both Plex and Jellyfin match by
    name+year via their own metadata agent by default; the tag is an
    optional disambiguation aid neither platform requires, and it makes the
    on-disk name noisier for no benefit once a library already resolves
    cleanly. A tool that still needs a title's TMDB id after organizing
    (e.g. `artwork` refreshing art later) reads it back out of the NFO
    sidecar's `<uniqueid type="tmdb">` instead of the path -- see
    `extract_provider_id`'s docstring for the one remaining place a tag in
    a *source* filename still matters.
  - Plex episode files:     `Series - S01E01 - Title.ext`
  - Jellyfin episode files: `Series (Year) S01E01 Title.ext`
    (episode files never carry the provider-ID tag on either platform --
    that was true before this change too, only the folder/movie-file tag
    has been dropped.)
"""

import re
from dataclasses import dataclass
from pathlib import Path

VALID_SERVERS = ("plex", "jellyfin")

# Characters Jellyfin explicitly reserves, and generally unsafe across the
# filesystems/network shares Plex and Jellyfin both commonly run on --
# stripped from every title-derived path component regardless of target.
_RESERVED_CHARS = '<>:"/\\|?*'
_PROVIDER_ID_RE = re.compile(r"(?:\{tmdb-(\d+)\}|\[tmdbid-(\d+)\])")


def sanitize_title(title: str) -> str:
    cleaned = "".join(c for c in title if c not in _RESERVED_CHARS)
    return " ".join(cleaned.split())  # collapse repeated/leading/trailing whitespace


def _check_server(server: str) -> None:
    if server not in VALID_SERVERS:
        raise ValueError(f"unknown media server {server!r}, expected one of {VALID_SERVERS}")


def extract_provider_id(name: str) -> int | None:
    """Read a `{tmdb-N}`/`[tmdbid-N]` tag out of a filename, if present.

    Organized folder/file names no longer carry this tag (see the module
    docstring), so this now only matters for an *inbox* filename that
    already has a known id hand-embedded before a fresh `organize` pass
    (`parse.py` reads it via guessit's own `tmdb_id` field, which recognizes
    the same syntax) -- e.g. to resolve a title guessit's fuzzy matcher
    can't. Kept as a fallback for legacy libraries organized before this
    change, or anyone who still tags a file by hand.
    """
    match = _PROVIDER_ID_RE.search(name)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


@dataclass(frozen=True)
class MovieMeta:
    title: str
    year: int | None
    tmdb_id: int


@dataclass(frozen=True)
class EpisodeMeta:
    series_title: str
    series_year: int | None
    series_tmdb_id: int
    season: int
    episode: int
    episode_title: str | None = None


def movie_base_name(meta: MovieMeta, server: str) -> str:
    _check_server(server)
    title = sanitize_title(meta.title)
    year_part = f" ({meta.year})" if meta.year else ""
    return f"{title}{year_part}"


def movie_folder(meta: MovieMeta, server: str, movies_root: str | Path) -> Path:
    return Path(movies_root) / movie_base_name(meta, server)


def movie_video_path(meta: MovieMeta, server: str, movies_root: str | Path, ext: str) -> Path:
    base = movie_base_name(meta, server)
    return movie_folder(meta, server, movies_root) / f"{base}{ext}"


def series_base_name(meta: EpisodeMeta, server: str) -> str:
    _check_server(server)
    title = sanitize_title(meta.series_title)
    year_part = f" ({meta.series_year})" if meta.series_year else ""
    return f"{title}{year_part}"


def series_folder(meta: EpisodeMeta, server: str, tv_root: str | Path) -> Path:
    return Path(tv_root) / series_base_name(meta, server)


def season_folder(meta: EpisodeMeta, server: str, tv_root: str | Path) -> Path:
    _check_server(server)
    return series_folder(meta, server, tv_root) / f"Season {meta.season:02d}"


def episode_base_name(meta: EpisodeMeta, server: str) -> str:
    _check_server(server)
    series_title = sanitize_title(meta.series_title)
    episode_title = sanitize_title(meta.episode_title) if meta.episode_title else None
    sxe = f"S{meta.season:02d}E{meta.episode:02d}"
    if server == "plex":
        name = f"{series_title} - {sxe}"
        if episode_title:
            name += f" - {episode_title}"
        return name
    # jellyfin
    year_part = f" ({meta.series_year})" if meta.series_year else ""
    name = f"{series_title}{year_part} {sxe}"
    if episode_title:
        name += f" {episode_title}"
    return name


def episode_video_path(meta: EpisodeMeta, server: str, tv_root: str | Path, ext: str) -> Path:
    base = episode_base_name(meta, server)
    return season_folder(meta, server, tv_root) / f"{base}{ext}"


def sidecar_path(video_path: str | Path, suffix: str) -> Path:
    """Path for a file that belongs next to `video_path` and shares its stem
    -- an NFO (`suffix=".nfo"`) or a subtitle (`suffix=".en.srt"`)."""
    video_path = Path(video_path)
    return video_path.with_name(video_path.stem + suffix)


def poster_path(folder: str | Path) -> Path:
    return Path(folder) / "poster.jpg"


def fanart_path(folder: str | Path) -> Path:
    return Path(folder) / "fanart.jpg"


def tvshow_nfo_path(folder: str | Path) -> Path:
    return Path(folder) / "tvshow.nfo"


def subtitle_path(video_path: str | Path, language: str) -> Path:
    """`language` is an ISO 639-1 code (e.g. "en") -- the convention both
    Plex and Jellyfin recognize for external subtitles."""
    return sidecar_path(video_path, f".{language}.srt")
