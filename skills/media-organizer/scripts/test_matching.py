from __future__ import annotations

from mediaorganizer import matching
from mediaorganizer.matching import Candidate


def test_no_candidates_gives_zero_confidence():
    result = matching.best_match("The Matrix", 1999, [])
    assert result.candidate is None
    assert result.confidence == 0.0


def test_exact_title_and_year_is_high_confidence():
    candidates = [Candidate(tmdb_id=603, title="The Matrix", year=1999)]
    result = matching.best_match("The Matrix", 1999, candidates)
    assert result.candidate is not None
    assert result.candidate.tmdb_id == 603
    assert result.confidence >= matching.MIN_AUTO_CONFIDENCE


def test_wrong_year_lowers_confidence_below_exact_match():
    candidates_right_year = [Candidate(tmdb_id=1, title="The Matrix", year=1999)]
    candidates_wrong_year = [Candidate(tmdb_id=2, title="The Matrix", year=1985)]
    right = matching.best_match("The Matrix", 1999, candidates_right_year)
    wrong = matching.best_match("The Matrix", 1999, candidates_wrong_year)
    assert wrong.confidence < right.confidence


def test_picks_best_of_multiple_ambiguous_candidates():
    candidates = [
        Candidate(tmdb_id=1, title="The Matrix Reloaded", year=2003),
        Candidate(tmdb_id=2, title="The Matrix", year=1999),
        Candidate(tmdb_id=3, title="The Matrix Revolutions", year=2003),
    ]
    result = matching.best_match("The Matrix", 1999, candidates)
    assert result.candidate is not None
    assert result.candidate.tmdb_id == 2


def test_unrelated_title_is_low_confidence():
    candidates = [Candidate(tmdb_id=1, title="Completely Different Film", year=1999)]
    result = matching.best_match("The Matrix", 1999, candidates)
    assert result.confidence < matching.MIN_AUTO_CONFIDENCE


def test_no_query_year_still_scores_on_title_alone():
    candidates = [Candidate(tmdb_id=1, title="The Matrix", year=1999)]
    result = matching.best_match("The Matrix", None, candidates)
    assert result.candidate is not None
    assert result.candidate.tmdb_id == 1
    assert result.confidence == matching.title_similarity("The Matrix", "The Matrix")


def test_title_similarity_is_case_and_punctuation_insensitive():
    assert matching.title_similarity("The Matrix", "the matrix") == 1.0
    assert matching.title_similarity("Se7en", "Se7en") == 1.0
    assert matching.title_similarity("Amélie", "Amelie") < 1.0  # accents aren't stripped
