import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from psammophis.medialib import naming
from psammophis.medialib.tmdb import TmdbClient
from psammophis.runtime.filesystem import atomic_write_text

from . import nfo

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})
ARTWORK_TYPES = ("all", "poster", "fanart", "still", "nfo")
_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,3})E(\d{1,4})\b")


class ArtworkError(RuntimeError):
    pass


class TmdbReader(Protocol):
    def movie_details(self, tmdb_id: int) -> dict: ...

    def tv_details(self, tmdb_id: int) -> dict: ...

    def episode_details(self, tv_id: int, season: int, episode: int) -> dict: ...


@dataclass(frozen=True)
class MediaIdentity:
    video: Path
    tmdb_id: int
    season: int | None = None
    episode: int | None = None

    @property
    def is_episode(self) -> bool:
        return self.season is not None and self.episode is not None


@dataclass(frozen=True)
class Write:
    kind: str
    destination: Path
    content: str | None = None
    image_path: str | None = None


def identify(video: Path, tmdb_id_override: int | None = None) -> MediaIdentity:
    episode_match = _EPISODE_RE.search(video.stem)
    tmdb_id = tmdb_id_override or naming.extract_provider_id(video.name)
    if tmdb_id is None:
        for parent in video.parents:
            tmdb_id = naming.extract_provider_id(parent.name)
            if tmdb_id is not None:
                break
    if tmdb_id is None:
        raise ArtworkError(f"no TMDB provider tag found for {video}")
    if episode_match is None:
        return MediaIdentity(video, tmdb_id)
    return MediaIdentity(
        video,
        tmdb_id,
        int(episode_match.group(1)),
        int(episode_match.group(2)),
    )


def _credits_names(credits: dict, job: str) -> list[str]:
    return [item["name"] for item in credits.get("crew", []) if item.get("job") == job]


def _movie_nfo(details: dict, tmdb_id: int) -> str:
    credits = details.get("credits", {})
    release_date = details.get("release_date")
    year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None
    return nfo.build_movie_nfo(
        nfo.MovieNfoData(
            title=details.get("title") or "Untitled",
            tmdb_id=tmdb_id,
            original_title=details.get("original_title"),
            year=year,
            plot=details.get("overview"),
            tagline=details.get("tagline"),
            runtime_minutes=details.get("runtime"),
            premiered=release_date,
            genres=[item["name"] for item in details.get("genres", [])],
            studios=[item["name"] for item in details.get("production_companies", [])],
            directors=_credits_names(credits, "Director"),
            writers=_credits_names(credits, "Writer") or _credits_names(credits, "Screenplay"),
            actors=[
                nfo.Actor(item["name"], item.get("character"), item.get("order"))
                for item in credits.get("cast", [])[:15]
            ],
            imdb_id=(details.get("external_ids") or {}).get("imdb_id"),
        )
    )


def build_plan(identity: MediaIdentity, tmdb: TmdbReader, artwork_type: str) -> list[Write]:
    if artwork_type not in ARTWORK_TYPES:
        raise ArtworkError(f"unknown artwork type: {artwork_type}")
    writes: list[Write] = []
    if not identity.is_episode:
        details = tmdb.movie_details(identity.tmdb_id)
        if artwork_type in ("all", "poster") and details.get("poster_path"):
            writes.append(
                Write(
                    "poster",
                    identity.video.parent / "poster.jpg",
                    image_path=details["poster_path"],
                )
            )
        if artwork_type in ("all", "fanart") and details.get("backdrop_path"):
            writes.append(
                Write(
                    "fanart",
                    identity.video.parent / "fanart.jpg",
                    image_path=details["backdrop_path"],
                )
            )
        if artwork_type in ("all", "nfo"):
            writes.append(
                Write(
                    "nfo",
                    naming.sidecar_path(identity.video, ".nfo"),
                    _movie_nfo(details, identity.tmdb_id),
                )
            )
        if artwork_type == "still":
            raise ArtworkError("still artwork is only valid for TV episodes")
        return writes

    assert identity.season is not None and identity.episode is not None
    series = tmdb.tv_details(identity.tmdb_id)
    episode = tmdb.episode_details(identity.tmdb_id, identity.season, identity.episode)
    series_dir = identity.video.parent.parent
    if artwork_type in ("all", "poster") and series.get("poster_path"):
        writes.append(Write("poster", series_dir / "poster.jpg", image_path=series["poster_path"]))
    if artwork_type in ("all", "fanart") and series.get("backdrop_path"):
        writes.append(
            Write("fanart", series_dir / "fanart.jpg", image_path=series["backdrop_path"])
        )
    if artwork_type in ("all", "still") and episode.get("still_path"):
        writes.append(
            Write(
                "still",
                naming.sidecar_path(identity.video, ".jpg"),
                image_path=episode["still_path"],
            )
        )
    if artwork_type in ("all", "nfo"):
        credits = episode.get("credits", {})
        writes.append(
            Write(
                "nfo",
                naming.sidecar_path(identity.video, ".nfo"),
                nfo.build_episode_nfo(
                    nfo.EpisodeNfoData(
                        title=episode.get("name") or f"Episode {identity.episode}",
                        show_title=series.get("name") or identity.video.parent.parent.name,
                        season=identity.season,
                        episode=identity.episode,
                        plot=episode.get("overview"),
                        aired=episode.get("air_date"),
                        directors=_credits_names(credits, "Director"),
                        writers=_credits_names(credits, "Writer")
                        or _credits_names(credits, "Screenplay"),
                        tmdb_id=identity.tmdb_id,
                    )
                ),
            )
        )
        writes.append(
            Write(
                "nfo",
                naming.tvshow_nfo_path(series_dir),
                nfo.build_tvshow_nfo(
                    nfo.ShowNfoData(
                        title=series.get("name") or series_dir.name,
                        tmdb_id=identity.tmdb_id,
                        plot=series.get("overview"),
                        premiered=series.get("first_air_date"),
                        genres=[item["name"] for item in series.get("genres", [])],
                        studios=[item["name"] for item in series.get("networks", [])],
                        imdb_id=(series.get("external_ids") or {}).get("imdb_id"),
                    )
                ),
            )
        )
    return writes


def execute(writes: list[Write], tmdb: TmdbClient) -> list[str]:
    warnings: list[str] = []
    for write in writes:
        write.destination.parent.mkdir(parents=True, exist_ok=True)
        if write.content is not None:
            atomic_write_text(write.destination, write.content)
        elif write.image_path and not tmdb.download_image(write.image_path, write.destination):
            warnings.append(f"{write.kind} download failed: {write.destination}")
    return warnings
