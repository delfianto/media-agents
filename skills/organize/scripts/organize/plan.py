import shutil
from dataclasses import dataclass
from pathlib import Path

from medialib import naming
from medialib.tmdb import TmdbClient, TmdbError

from . import matching
from .config import Config
from .parse import ParsedName, parse

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})


@dataclass
class OrganizeResult:
    source: Path
    status: str
    detail: str = ""
    destination: Path | None = None
    backup: Path | None = None


@dataclass
class Plan:
    kind: str
    source: Path
    tmdb_id: int
    video_path: Path
    confidence: float = 1.0
    match_reason: str = ""


def _year_from_date(date_str: str | None) -> int | None:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


def _movie_candidates(results: list[dict]) -> list[matching.Candidate]:
    return [
        matching.Candidate(
            tmdb_id=result["id"],
            title=result.get("title") or result.get("original_title") or "",
            year=_year_from_date(result.get("release_date")),
        )
        for result in results
        if result.get("id") is not None
    ]


def _tv_candidates(results: list[dict]) -> list[matching.Candidate]:
    return [
        matching.Candidate(
            tmdb_id=result["id"],
            title=result.get("name") or result.get("original_name") or "",
            year=_year_from_date(result.get("first_air_date")),
        )
        for result in results
        if result.get("id") is not None
    ]


def identify_movie(
    tmdb: TmdbClient, parsed: ParsedName, min_confidence: float
) -> tuple[int | None, float, str]:
    if parsed.tmdb_id is not None:
        return parsed.tmdb_id, 1.0, "tmdb id already embedded in filename"
    match = matching.best_match(
        parsed.title,
        parsed.year,
        _movie_candidates(tmdb.search_movie(parsed.title, parsed.year)),
    )
    if match.candidate is None or match.confidence < min_confidence:
        return None, match.confidence, match.reason
    return match.candidate.tmdb_id, match.confidence, match.reason


def identify_series(
    tmdb: TmdbClient, parsed: ParsedName, min_confidence: float
) -> tuple[int | None, float, str]:
    if parsed.tmdb_id is not None:
        return parsed.tmdb_id, 1.0, "tmdb id already embedded in filename"
    match = matching.best_match(
        parsed.title,
        parsed.year,
        _tv_candidates(tmdb.search_tv(parsed.title, parsed.year)),
    )
    if match.candidate is None or match.confidence < min_confidence:
        return None, match.confidence, match.reason
    return match.candidate.tmdb_id, match.confidence, match.reason


def build_movie_plan(cfg: Config, tmdb: TmdbClient, source: Path) -> Plan | OrganizeResult:
    parsed = parse(source)
    if parsed is None or parsed.kind != "movie":
        return OrganizeResult(source, "error", "could not parse as a movie filename")
    tmdb_id, confidence, reason = identify_movie(tmdb, parsed, cfg.min_confidence)
    if tmdb_id is None:
        return OrganizeResult(
            source, "review", f"no confident match (confidence={confidence:.2f}): {reason}"
        )
    details = tmdb.movie_details(tmdb_id)
    meta = naming.MovieMeta(
        title=details.get("title") or parsed.title,
        year=_year_from_date(details.get("release_date")) or parsed.year,
        tmdb_id=tmdb_id,
    )
    return Plan(
        "movie",
        source,
        tmdb_id,
        naming.movie_video_path(meta, cfg.media_server, cfg.movies_dir, source.suffix),
        confidence,
        reason,
    )


def build_episode_plan(cfg: Config, tmdb: TmdbClient, source: Path) -> Plan | OrganizeResult:
    parsed = parse(source)
    if (
        parsed is None
        or parsed.kind != "episode"
        or parsed.season is None
        or parsed.episode is None
    ):
        return OrganizeResult(source, "error", "could not parse as a TV episode filename")
    tmdb_id, confidence, reason = identify_series(tmdb, parsed, cfg.min_confidence)
    if tmdb_id is None:
        return OrganizeResult(
            source, "review", f"no confident match (confidence={confidence:.2f}): {reason}"
        )
    series = tmdb.tv_details(tmdb_id)
    try:
        episode = tmdb.episode_details(tmdb_id, parsed.season, parsed.episode)
    except TmdbError as exc:
        return OrganizeResult(source, "error", f"episode lookup failed: {exc}")
    meta = naming.EpisodeMeta(
        series_title=series.get("name") or parsed.title,
        series_year=_year_from_date(series.get("first_air_date")),
        series_tmdb_id=tmdb_id,
        season=parsed.season,
        episode=parsed.episode,
        episode_title=episode.get("name") or parsed.episode_title,
    )
    return Plan(
        "episode",
        source,
        tmdb_id,
        naming.episode_video_path(meta, cfg.media_server, cfg.tv_shows_dir, source.suffix),
        confidence,
        reason,
    )


def _next_backup(path: Path) -> Path:
    candidate = path.with_name(path.name + ".bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{counter}")
        counter += 1
    return candidate


def execute_plan(
    plan: Plan, *, copy_instead_of_move: bool = False, overwrite: bool = False
) -> OrganizeResult:
    backup = None
    if plan.video_path.exists():
        if not overwrite:
            return OrganizeResult(
                plan.source, "error", f"destination already exists: {plan.video_path}"
            )
        backup = _next_backup(plan.video_path)
        shutil.move(plan.video_path, backup)

    plan.video_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if copy_instead_of_move:
            shutil.copy2(plan.source, plan.video_path)
        else:
            shutil.move(plan.source, plan.video_path)
    except OSError as exc:
        if backup is not None:
            plan.video_path.unlink(missing_ok=True)
            shutil.move(backup, plan.video_path)
        return OrganizeResult(plan.source, "error", str(exc), backup=backup)
    return OrganizeResult(plan.source, "moved", "ok", destination=plan.video_path, backup=backup)
