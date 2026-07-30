import re
from dataclasses import dataclass
from pathlib import Path

from medialib import naming

from .opensubtitles import OpenSubtitlesClient, OpenSubtitlesError

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})
_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,3})E(\d{1,4})\b")


class SubtitleError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubtitlePlan:
    video: Path
    tmdb_id: int
    language: str
    destination: Path
    season: int | None = None
    episode: int | None = None


def build_plans(video: Path, languages: tuple[str, ...]) -> list[SubtitlePlan]:
    match = _EPISODE_RE.search(video.stem)
    tmdb_id = naming.extract_provider_id(video.name)
    if tmdb_id is None:
        for parent in video.parents:
            tmdb_id = naming.extract_provider_id(parent.name)
            if tmdb_id is not None:
                break
    if tmdb_id is None:
        raise SubtitleError(f"no TMDB provider tag found for {video}")
    season = int(match.group(1)) if match else None
    episode = int(match.group(2)) if match else None
    return [
        SubtitlePlan(
            video,
            tmdb_id,
            language,
            naming.subtitle_path(video, language),
            season,
            episode,
        )
        for language in languages
    ]


def execute(plan: SubtitlePlan, client: OpenSubtitlesClient, *, overwrite: bool = False) -> str:
    if plan.destination.exists() and not overwrite:
        return "skipped (already exists)"
    try:
        results = client.search(
            tmdb_id=plan.tmdb_id,
            languages=plan.language,
            season_number=plan.season,
            episode_number=plan.episode,
            media_type="episode" if plan.season is not None else "movie",
        )
        if not results:
            return "not found"
        file_id = results[0]["attributes"]["files"][0]["file_id"]
        plan.destination.parent.mkdir(parents=True, exist_ok=True)
        client.download_to(file_id, plan.destination)
    except (OpenSubtitlesError, KeyError, IndexError) as exc:
        raise SubtitleError(str(exc)) from exc
    return "downloaded"
