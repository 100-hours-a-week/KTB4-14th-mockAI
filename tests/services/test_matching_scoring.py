from fractions import Fraction
from itertools import combinations
from math import floor

import pytest

from app.services.matching_scoring import is_pace_compatible, match_rate, pace_points

THEMES = ("NATURE", "FOOD", "CULTURE", "REST", "SNS", "ACTIVITY")
PACES = ("RELAXED", "BALANCED", "PACKED")


def expected_rate(request_themes, candidate_themes, request_pace, candidate_pace):
    gap = abs(PACES.index(request_pace) - PACES.index(candidate_pace))
    if gap == 2:
        return None
    pace = Fraction(1) if gap == 0 else Fraction(1, 2)
    theme = Fraction(len(set(request_themes) & set(candidate_themes)), len(request_themes))
    score = 100 * (Fraction(4, 5) * theme + Fraction(1, 5) * pace)
    return floor(score + Fraction(1, 2))


@pytest.mark.parametrize(
    ("request_pace", "candidate_pace", "expected_points", "eligible"),
    [
        ("RELAXED", "RELAXED", 20, True),
        ("RELAXED", "BALANCED", 10, True),
        ("RELAXED", "PACKED", None, False),
        ("BALANCED", "RELAXED", 10, True),
        ("BALANCED", "BALANCED", 20, True),
        ("BALANCED", "PACKED", 10, True),
        ("PACKED", "RELAXED", None, False),
        ("PACKED", "BALANCED", 10, True),
        ("PACKED", "PACKED", 20, True),
    ],
)
def test_all_pace_pairs(request_pace, candidate_pace, expected_points, eligible):
    assert pace_points(request_pace, candidate_pace) == expected_points
    assert is_pace_compatible(request_pace, candidate_pace) is eligible


def test_spec_examples_and_three_theme_half_up_rounding():
    assert match_rate(["NATURE", "FOOD"], ["NATURE", "FOOD", "REST"], "RELAXED", "RELAXED") == 100
    assert match_rate(["NATURE", "FOOD"], ["NATURE", "FOOD"], "RELAXED", "BALANCED") == 90
    assert match_rate(["NATURE", "FOOD"], ["NATURE", "CULTURE"], "RELAXED", "RELAXED") == 60
    assert match_rate(["NATURE", "FOOD", "CULTURE"], ["NATURE"], "RELAXED", "RELAXED") == 47


def test_opposite_pace_returns_none():
    assert match_rate(["NATURE"], ["NATURE"], "RELAXED", "PACKED") is None


def test_every_valid_theme_subset_matches_independent_fraction_calculation():
    for request_size in range(1, 4):
        for candidate_size in range(1, 5):
            for request_themes in combinations(THEMES, request_size):
                for candidate_themes in combinations(THEMES, candidate_size):
                    for request_pace in PACES:
                        for candidate_pace in PACES:
                            assert match_rate(
                                request_themes, candidate_themes, request_pace, candidate_pace,
                            ) == expected_rate(request_themes, candidate_themes, request_pace, candidate_pace)


def test_empty_request_themes_are_rejected_by_the_pure_function():
    with pytest.raises(ValueError, match="must not be empty"):
        match_rate([], ["NATURE"], "RELAXED", "RELAXED")
