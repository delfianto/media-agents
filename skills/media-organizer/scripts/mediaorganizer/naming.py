"""Destination path/filename builders for Plex and Jellyfin's naming
conventions. Pure string/path formatting -- no I/O, no network calls.

Conventions (see reference/naming-conventions.md for the full citations):
  - Plex:     folder/file provider-ID tag is `{tmdb-12345}` (curly braces,
              hyphen). Episode files: `Series - s01e01 - Title.ext` (no ID
              tag on the episode file itself, only on the series folder).
  - Jellyfin: folder/file provider-ID tag is `[tmdbid-12345]` (square
              brackets, no hyphen before the digits). Episode files:
              `Series (Year) S01E01 Title.ext` (space-separated, no dashes).

Both platforms want the provider-ID tag on a movie's *file* name as well as
its folder (Jellyfin: "video file ... same name as the folder"; Plex:
tolerates either but the same-name convention is the safe superset), but
only on the *folder* for a TV series, never on individual episode files.
"""

from dataclasses import dataclass
from pathlib import Path

VALID_SERVERS = ("plex", "jellyfin")

# Characters Jellyfin explicitly reserves, and generally unsafe across the
# filesystems/network shares Plex and Jellyfin both commonly run on --
# stripped from every title-derived path component regardless of target.
_RESERVED_CHARS = '<>:"/\\|?*'


def sanitize_title(title: str) -> str:
    cleaned = "".join(c for c in title if c not in _RESERVED_CHARS)
    return " ".join(cleaned.split())  # collapse repeated/leading/trailing whitespace


def _check_server(server: str) -> None:
    if server not in VALID_SERVERS:
        raise ValueError(f"unknown media server {server!r}, expected one of {VALID_SERVERS}")


def _id_tag(server: str, tmdb_id: int) -> str:
    _check_server(server)
    return f"{{tmdb-{tmdb_id}}}" if server == "plex" else f"[tmdbid-{tmdb_id}]"


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
    title = sanitize_title(meta.title)
    year_part = f" ({meta.year})" if meta.year else ""
    tag = _id_tag(server, meta.tmdb_id)
    return f"{title}{year_part} {tag}"


def movie_folder(meta: MovieMeta, server: str, movies_root: str | Path) -> Path:
    return Path(movies_root) / movie_base_name(meta, server)


def movie_video_path(meta: MovieMeta, server: str, movies_root: str | Path, ext: str) -> Path:
    base = movie_base_name(meta, server)
    return movie_folder(meta, server, movies_root) / f"{base}{ext}"


def series_base_name(meta: EpisodeMeta, server: str) -> str:
    title = sanitize_title(meta.series_title)
    year_part = f" ({meta.series_year})" if meta.series_year else ""
    tag = _id_tag(server, meta.series_tmdb_id)
    return f"{title}{year_part} {tag}"


def series_folder(meta: EpisodeMeta, server: str, tv_root: str | Path) -> Path:
    return Path(tv_root) / series_base_name(meta, server)


def season_folder(meta: EpisodeMeta, server: str, tv_root: str | Path) -> Path:
    _check_server(server)
    return series_folder(meta, server, tv_root) / f"Season {meta.season:02d}"


def episode_base_name(meta: EpisodeMeta, server: str) -> str:
    _check_server(server)
    series_title = sanitize_title(meta.series_title)
    episode_title = sanitize_title(meta.episode_title) if meta.episode_title else None
    if server == "plex":
        sxe = f"s{meta.season:02d}e{meta.episode:02d}"
        name = f"{series_title} - {sxe}"
        if episode_title:
            name += f" - {episode_title}"
        return name
    # jellyfin
    sxe = f"S{meta.season:02d}E{meta.episode:02d}"
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
