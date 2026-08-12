import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib import naming

from .opensubtitles import OpenSubtitlesClient, OpenSubtitlesError

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})
_EPISODE_RE = re.compile(r"(?i)\bS(\d{1,3})E(\d{1,4})\b")

# OpenSubtitles' default search ranks candidates by nothing in particular --
# confirmed on this library: American Gods S01E01 returned 17 real results,
# and result[0] (what this module used to take unconditionally) was a
# 29.97fps DVDRip against a 23.976fps source. Its subtitle timing ran out at
# 59:17 into a 62:44 episode, silently missing the last ~3.5 minutes of
# actual dialogue -- confirmed by ffmpeg silencedetect finding continuous
# non-silent audio well past the last subtitle cue. An fps mismatch is the
# single strongest, cheapest-to-check signal that a candidate was timed for
# a differently-paced release (PAL/NTSC conversion, DVD vs BluRay/WEB, ...);
# ratings/download_count are useful only as a tiebreaker among fps-matching
# candidates, not a substitute for actually checking fps.
_FPS_TOLERANCE = 0.05


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
    video_fps: float | None = None


def probe_fps(video: Path) -> float | None:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        return None
    raw = streams[0].get("r_frame_rate")
    if not raw or "/" not in raw:
        return None
    num, _, den = raw.partition("/")
    try:
        den_value = float(den)
        return float(num) / den_value if den_value else None
    except ValueError:
        return None


def build_plans(video: Path, languages: tuple[str, ...]) -> list[SubtitlePlan]:
    match = _EPISODE_RE.search(video.stem)
    tmdb_id = naming.resolve_tmdb_id(video)
    if tmdb_id is None:
        raise SubtitleError(
            f"no TMDB provider tag found for {video} (checked the path and its NFO sidecar)"
        )
    season = int(match.group(1)) if match else None
    episode = int(match.group(2)) if match else None
    video_fps = probe_fps(video)
    return [
        SubtitlePlan(
            video,
            tmdb_id,
            language,
            naming.subtitle_path(video, language),
            season,
            episode,
            video_fps,
        )
        for language in languages
    ]


def _result_files(result: dict) -> list[dict]:
    return result.get("attributes", {}).get("files", []) or []


def _episode_matches(result: dict, season: int | None, episode: int | None) -> bool:
    """True unless the result's own structured season/episode contradicts
    the request.

    OpenSubtitles' season_number/episode_number search filters aren't
    airtight -- a live query for tmdb_id=46639 (American Gods) S01E01
    returned a result whose *filename* said "S01E03" among 16 correctly
    filed as S01E01. That result's own `feature_details.episode_number`
    still said 1 (only the uploader's filename was wrong), so this can't
    catch a mislabeled-filename case -- but it's a defensive check against
    the search endpoint (or a future response shape) actually handing back
    a different episode's subtitle outright, which would be a far worse
    failure than an fps mismatch: wrong content, not just wrong timing.
    Movies (season/episode both None) and results with no feature_details
    at all pass through unfiltered -- there's nothing to check."""
    if season is None or episode is None:
        return True
    details = result.get("attributes", {}).get("feature_details") or {}
    result_season = details.get("season_number")
    result_episode = details.get("episode_number")
    if result_season is None or result_episode is None:
        return True
    return result_season == season and result_episode == episode


def select_result(
    results: list[dict],
    target_fps: float | None,
    *,
    season: int | None = None,
    episode: int | None = None,
) -> dict | None:
    """Pick the best OpenSubtitles search result, not just the first one.

    Filtered in order of how bad a wrong pick would be: season/episode
    match first (wrong content is worse than wrong timing), then fps match
    against the source video (see the module docstring above for why);
    download_count is only a tiebreaker among whatever candidates survive
    both filters (or, if a filter would eliminate every candidate / its
    target is unknown, it's skipped rather than emptying the pool)."""
    usable = [r for r in results if _result_files(r)]
    if not usable:
        return None
    episode_matches = [r for r in usable if _episode_matches(r, season, episode)]
    if episode_matches:
        usable = episode_matches
    if target_fps is not None:
        fps_matches = [
            r
            for r in usable
            if abs((r.get("attributes", {}).get("fps") or 0.0) - target_fps) <= _FPS_TOLERANCE
        ]
        if fps_matches:
            usable = fps_matches
    return max(usable, key=lambda r: r.get("attributes", {}).get("download_count") or 0)


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
        best = select_result(results, plan.video_fps, season=plan.season, episode=plan.episode)
        if best is None:
            return "not found"
        file_id = _result_files(best)[0]["file_id"]
        plan.destination.parent.mkdir(parents=True, exist_ok=True)
        client.download_to(file_id, plan.destination)
    except (OpenSubtitlesError, OSError, KeyError, IndexError) as exc:
        raise SubtitleError(str(exc)) from exc
    return "downloaded"
