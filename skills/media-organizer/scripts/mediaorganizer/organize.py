"""Per-file organize orchestration: parse -> identify (TMDB) -> plan
(destination path, NFO, artwork, subtitles) -> execute. Dry-run by default;
--yes actually moves/copies the file and fetches artwork/subtitles/writes
the NFO.

Confidence-gated, with no safe fallback: a match below
`config.min_confidence` is left alone and reported for manual review rather
than acted on. Unlike track-strip's audio-track safety net (which always
has a "keep the original track" fallback), there is no safe default here --
misidentifying a movie and renaming it to the wrong title has no equivalent
of "leave it as it was."
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import matching, naming, nfo
from .config import Config
from .opensubtitles import OpenSubtitlesClient, OpenSubtitlesError
from .parse import ParsedName, parse
from .tmdb import TmdbClient, TmdbError

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})


@dataclass
class OrganizeResult:
    source: Path
    status: str  # "planned" | "moved" | "review" | "error"
    detail: str = ""
    destination: Path | None = None


@dataclass
class Plan:
    kind: str  # "movie" | "episode"
    source: Path
    tmdb_id: int
    video_path: Path
    nfo_path: Path
    nfo_content: str
    tvshow_nfo: tuple[Path, str] | None  # only set the first time a series folder is created
    poster_path: Path | None
    poster_tmdb_path: str | None  # raw API-relative path, e.g. "/abc123.jpg"
    fanart_path: Path | None
    fanart_tmdb_path: str | None
    subtitle_plan: list[tuple[str, Path]] = field(default_factory=list)  # (language, dest)
    confidence: float = 1.0
    match_reason: str = ""
    season: int | None = None  # set for kind="episode"; tmdb_id is the *series* id there
    episode: int | None = None


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
            tmdb_id=r["id"],
            title=r.get("title") or r.get("original_title") or "",
            year=_year_from_date(r.get("release_date")),
        )
        for r in results
        if r.get("id") is not None
    ]


def _tv_candidates(results: list[dict]) -> list[matching.Candidate]:
    return [
        matching.Candidate(
            tmdb_id=r["id"],
            title=r.get("name") or r.get("original_name") or "",
            year=_year_from_date(r.get("first_air_date")),
        )
        for r in results
        if r.get("id") is not None
    ]


def identify_movie(
    tmdb: TmdbClient, parsed: ParsedName, min_confidence: float
) -> tuple[int | None, float, str]:
    if parsed.tmdb_id is not None:
        return parsed.tmdb_id, 1.0, "tmdb id already embedded in filename"
    results = tmdb.search_movie(parsed.title, parsed.year)
    match = matching.best_match(parsed.title, parsed.year, _movie_candidates(results))
    if match.candidate is None or match.confidence < min_confidence:
        return None, match.confidence, match.reason
    return match.candidate.tmdb_id, match.confidence, match.reason


def identify_series(
    tmdb: TmdbClient, parsed: ParsedName, min_confidence: float
) -> tuple[int | None, float, str]:
    if parsed.tmdb_id is not None:
        return parsed.tmdb_id, 1.0, "tmdb id already embedded in filename"
    results = tmdb.search_tv(parsed.title, parsed.year)
    match = matching.best_match(parsed.title, parsed.year, _tv_candidates(results))
    if match.candidate is None or match.confidence < min_confidence:
        return None, match.confidence, match.reason
    return match.candidate.tmdb_id, match.confidence, match.reason


def _credits_names(credits: dict, job_or_department: str, key: str = "job") -> list[str]:
    return [c["name"] for c in credits.get("crew", []) if c.get(key) == job_or_department]


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
    ext = source.suffix
    meta = naming.MovieMeta(
        title=details.get("title") or parsed.title,
        year=_year_from_date(details.get("release_date")) or parsed.year,
        tmdb_id=tmdb_id,
    )
    folder = naming.movie_folder(meta, cfg.media_server, cfg.movies_dir)
    video_path = naming.movie_video_path(meta, cfg.media_server, cfg.movies_dir, ext)

    credits = details.get("credits", {})
    nfo_data = nfo.MovieNfoData(
        title=meta.title,
        tmdb_id=tmdb_id,
        original_title=details.get("original_title"),
        year=meta.year,
        plot=details.get("overview"),
        tagline=details.get("tagline"),
        runtime_minutes=details.get("runtime"),
        premiered=details.get("release_date"),
        genres=[g["name"] for g in details.get("genres", [])],
        studios=[c["name"] for c in details.get("production_companies", [])],
        directors=_credits_names(credits, "Director"),
        writers=_credits_names(credits, "Writer") or _credits_names(credits, "Screenplay"),
        actors=[
            nfo.Actor(name=a["name"], role=a.get("character"), order=a.get("order"))
            for a in credits.get("cast", [])[:15]
        ],
        imdb_id=(details.get("external_ids") or {}).get("imdb_id"),
    )

    poster_path = naming.poster_path(folder) if details.get("poster_path") else None
    fanart_path = naming.fanart_path(folder) if details.get("backdrop_path") else None

    return Plan(
        kind="movie",
        source=source,
        tmdb_id=tmdb_id,
        video_path=video_path,
        nfo_path=naming.sidecar_path(video_path, ".nfo"),
        nfo_content=nfo.build_movie_nfo(nfo_data),
        tvshow_nfo=None,
        poster_path=poster_path,
        poster_tmdb_path=details.get("poster_path"),
        fanart_path=fanart_path,
        fanart_tmdb_path=details.get("backdrop_path"),
        subtitle_plan=[
            (lang, naming.subtitle_path(video_path, lang)) for lang in cfg.subtitle_languages
        ],
        confidence=confidence,
        match_reason=reason,
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

    series_details = tmdb.tv_details(tmdb_id)
    try:
        episode_details = tmdb.episode_details(tmdb_id, parsed.season, parsed.episode)
    except TmdbError as exc:
        return OrganizeResult(source, "error", f"episode lookup failed: {exc}")

    ext = source.suffix
    meta = naming.EpisodeMeta(
        series_title=series_details.get("name") or parsed.title,
        series_year=_year_from_date(series_details.get("first_air_date")),
        series_tmdb_id=tmdb_id,
        season=parsed.season,
        episode=parsed.episode,
        episode_title=episode_details.get("name") or parsed.episode_title,
    )
    series_dir = naming.series_folder(meta, cfg.media_server, cfg.tv_shows_dir)
    video_path = naming.episode_video_path(meta, cfg.media_server, cfg.tv_shows_dir, ext)

    tvshow_nfo_path = naming.tvshow_nfo_path(series_dir)
    tvshow_nfo_content = None
    if not tvshow_nfo_path.exists():
        show_data = nfo.ShowNfoData(
            title=meta.series_title,
            tmdb_id=tmdb_id,
            plot=series_details.get("overview"),
            premiered=series_details.get("first_air_date"),
            genres=[g["name"] for g in series_details.get("genres", [])],
            studios=[n["name"] for n in series_details.get("networks", [])],
            imdb_id=(series_details.get("external_ids") or {}).get("imdb_id"),
        )
        tvshow_nfo_content = nfo.build_tvshow_nfo(show_data)

    credits = episode_details.get("credits", {})
    ep_nfo_data = nfo.EpisodeNfoData(
        title=meta.episode_title or f"Episode {meta.episode}",
        show_title=meta.series_title,
        season=meta.season,
        episode=meta.episode,
        plot=episode_details.get("overview"),
        aired=episode_details.get("air_date"),
        directors=_credits_names(credits, "Director"),
        writers=_credits_names(credits, "Writer") or _credits_names(credits, "Screenplay"),
        tmdb_id=tmdb_id,
    )

    poster_path = naming.poster_path(series_dir) if series_details.get("poster_path") else None
    fanart_path = naming.fanart_path(series_dir) if series_details.get("backdrop_path") else None

    return Plan(
        kind="episode",
        source=source,
        tmdb_id=tmdb_id,
        video_path=video_path,
        nfo_path=naming.sidecar_path(video_path, ".nfo"),
        nfo_content=nfo.build_episode_nfo(ep_nfo_data),
        tvshow_nfo=(tvshow_nfo_path, tvshow_nfo_content) if tvshow_nfo_content else None,
        poster_path=poster_path,
        poster_tmdb_path=series_details.get("poster_path"),
        fanart_path=fanart_path,
        fanart_tmdb_path=series_details.get("backdrop_path"),
        subtitle_plan=[
            (lang, naming.subtitle_path(video_path, lang)) for lang in cfg.subtitle_languages
        ],
        confidence=confidence,
        match_reason=reason,
        season=meta.season,
        episode=meta.episode,
    )


def _fetch_subtitles(opensubs: OpenSubtitlesClient | None, plan: Plan) -> list[str]:
    warnings = []
    if opensubs is None:
        return warnings
    for language, dest in plan.subtitle_plan:
        try:
            results = opensubs.search(
                tmdb_id=plan.tmdb_id,
                languages=language,
                season_number=plan.season,
                episode_number=plan.episode,
                media_type="episode" if plan.kind == "episode" else "movie",
            )
            if not results:
                warnings.append(f"no {language} subtitles found")
                continue
            file_id = results[0]["attributes"]["files"][0]["file_id"]
            opensubs.download_to(file_id, dest)
        except (OpenSubtitlesError, KeyError, IndexError) as exc:
            warnings.append(f"{language} subtitle fetch failed: {exc}")
    return warnings


def execute_plan(
    plan: Plan,
    tmdb: TmdbClient,
    opensubs: OpenSubtitlesClient | None,
    copy_instead_of_move: bool = False,
) -> OrganizeResult:
    if plan.video_path.exists():
        return OrganizeResult(
            plan.source, "error", f"destination already exists: {plan.video_path}"
        )

    plan.video_path.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if plan.tvshow_nfo is not None:
        tvshow_path, tvshow_content = plan.tvshow_nfo
        tvshow_path.write_text(tvshow_content, encoding="utf-8")

    if (
        plan.poster_path is not None
        and plan.poster_tmdb_path is not None
        and not tmdb.download_image(plan.poster_tmdb_path, plan.poster_path)
    ):
        warnings.append("poster download failed")
    if (
        plan.fanart_path is not None
        and plan.fanart_tmdb_path is not None
        and not tmdb.download_image(plan.fanart_tmdb_path, plan.fanart_path)
    ):
        warnings.append("fanart download failed")

    plan.nfo_path.write_text(plan.nfo_content, encoding="utf-8")

    warnings += _fetch_subtitles(opensubs, plan)

    if copy_instead_of_move:
        shutil.copy2(plan.source, plan.video_path)
    else:
        shutil.move(str(plan.source), str(plan.video_path))

    detail = "; ".join(warnings) if warnings else "ok"
    return OrganizeResult(plan.source, "moved", detail, destination=plan.video_path)
