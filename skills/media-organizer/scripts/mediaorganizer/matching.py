"""Picks the best TMDB search result for a parsed filename, with a
confidence score. Pure logic over already-fetched search results (tmdb.py
does the actual network call) -- no I/O here, so this is unit-testable
without a TMDB API key.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

# Below this, don't auto-apply the match -- surface it for manual review
# instead of guessing wrong on an unattended/harness-driven run. This is the
# same "don't act on an ambiguous signal" principle as track-strip's SDH
# heuristic (bounded ratio, narrow eligibility) and its zero-audio-track
# safety net: better to leave a file untouched than to silently mis-rename
# or mis-tag it.
MIN_AUTO_CONFIDENCE = 0.75

_YEAR_MATCH_BONUS = 0.15
_YEAR_MISMATCH_PENALTY_PER_YEAR = 0.1
_MAX_YEAR_MISMATCH_PENALTY = 0.5


def _normalize(title: str) -> str:
    return "".join(c.lower() for c in title if c.isalnum() or c.isspace()).strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass(frozen=True)
class Candidate:
    tmdb_id: int
    title: str
    year: int | None


@dataclass(frozen=True)
class MatchResult:
    candidate: Candidate | None
    confidence: float  # 0.0-1.0
    reason: str


def best_match(
    query_title: str, query_year: int | None, candidates: list[Candidate]
) -> MatchResult:
    if not candidates:
        return MatchResult(None, 0.0, "no search results")

    scored: list[tuple[float, Candidate, float]] = []
    for c in candidates:
        sim = title_similarity(query_title, c.title)
        adjustment = 0.0
        if query_year is not None and c.year is not None:
            if c.year == query_year:
                adjustment = _YEAR_MATCH_BONUS
            else:
                delta = abs(c.year - query_year)
                adjustment = -min(
                    _MAX_YEAR_MISMATCH_PENALTY, _YEAR_MISMATCH_PENALTY_PER_YEAR * delta
                )
        score = max(0.0, min(1.0, sim + adjustment))
        scored.append((score, c, sim))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    best_score, best_candidate, best_sim = scored[0]
    reason = f"title similarity {best_sim:.2f}"
    if query_year is not None:
        reason += f", year query={query_year} candidate={best_candidate.year}"
    return MatchResult(best_candidate, best_score, reason)
