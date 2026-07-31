import os
from dataclasses import dataclass
from pathlib import Path

from psammophis.medialib import naming
from psammophis.medialib.tmdb import TmdbClient, TmdbError
from psammophis.runtime.filesystem import (
    RecoveryRequired,
    copy_to_temporary,
    discard_staged_backup,
    fsync_directory,
    install_no_replace,
    path_exists,
    restore_from_backup,
    stage_backup,
)
from psammophis.runtime.signals import CancellationRequested

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
    while path_exists(candidate):
        candidate = path.with_name(f"{path.name}.bak.{counter}")
        counter += 1
    return candidate


def execute_plan(
    plan: Plan, *, copy_instead_of_move: bool = False, overwrite: bool = False
) -> OrganizeResult:
    backup = None
    destination_existed = path_exists(plan.video_path)
    if destination_existed and plan.video_path.is_symlink():
        return OrganizeResult(
            plan.source,
            "error",
            f"destination is a symlink, refusing to replace it: {plan.video_path}",
        )
    if destination_existed and plan.source.samefile(plan.video_path):
        return OrganizeResult(plan.source, "error", "source and destination are the same file")
    if destination_existed:
        if not overwrite:
            return OrganizeResult(
                plan.source, "error", f"destination already exists: {plan.video_path}"
            )
        backup = _next_backup(plan.video_path)

    plan.video_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    backup_staged = False
    try:
        temporary = copy_to_temporary(plan.source, plan.video_path)
        if backup is not None:
            stage_backup(plan.video_path, backup)
            backup_staged = True
            os.replace(temporary, plan.video_path)
            fsync_directory(plan.video_path.parent)
        else:
            install_no_replace(temporary, plan.video_path)
        if not copy_instead_of_move:
            plan.source.unlink()
            fsync_directory(plan.source.parent)
    except RecoveryRequired:
        raise
    except KeyboardInterrupt, SystemExit, CancellationRequested:
        _rollback_incomplete_move(
            source=plan.source,
            destination=plan.video_path,
            temporary=temporary,
            destination_existed=destination_existed,
            backup=backup,
            backup_staged=backup_staged,
        )
        raise
    except OSError as exc:
        _rollback_incomplete_move(
            source=plan.source,
            destination=plan.video_path,
            temporary=temporary,
            destination_existed=destination_existed,
            backup=backup,
            backup_staged=backup_staged,
        )
        return OrganizeResult(
            plan.source,
            "error",
            str(exc),
            backup=backup if backup is not None and path_exists(backup) else None,
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return OrganizeResult(plan.source, "moved", "ok", destination=plan.video_path, backup=backup)


def _rollback_incomplete_move(
    *,
    source: Path,
    destination: Path,
    temporary: Path | None,
    destination_existed: bool,
    backup: Path | None,
    backup_staged: bool,
) -> None:
    try:
        if not path_exists(source):
            return
        installed = temporary is not None and not path_exists(temporary)
        if installed:
            if destination_existed and backup is not None and backup_staged:
                restore_from_backup(backup, destination)
            elif not destination_existed:
                destination.unlink(missing_ok=True)
                fsync_directory(destination.parent)
        if backup is not None and backup_staged and path_exists(backup):
            discard_staged_backup(backup)
    except RecoveryRequired:
        raise
    except OSError as exc:
        recovery = f"; backup remains at {backup}" if backup is not None else ""
        raise RecoveryRequired(
            f"organize rollback failed: {exc}; source remains at {source}{recovery}"
        ) from exc
