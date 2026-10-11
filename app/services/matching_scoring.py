"""Pure arithmetic for the versioned theme-and-pace matching score."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.matching import Pace, Theme

PACE_LEVELS: dict[Pace, int] = {
    "RELAXED": 0,
    "BALANCED": 1,
    "PACKED": 2,
}


def is_pace_compatible(request_pace: Pace, candidate_pace: Pace) -> bool:
    """Return whether a candidate is not at the opposite travel-speed extreme."""
    return abs(PACE_LEVELS[request_pace] - PACE_LEVELS[candidate_pace]) < 2


def pace_points(request_pace: Pace, candidate_pace: Pace) -> int | None:
    """Return the pace contribution on a 0..20 scale, or ``None`` if excluded."""
    gap = abs(PACE_LEVELS[request_pace] - PACE_LEVELS[candidate_pace])
    if gap == 2:
        return None
    return 20 if gap == 0 else 10


def match_rate(
    request_themes: list[Theme] | tuple[Theme, ...],
    candidate_themes: list[Theme] | tuple[Theme, ...],
    request_pace: Pace,
    candidate_pace: Pace,
) -> int | None:
    """Calculate the specified half-up integer rate, or ``None`` for incompatible pace.

    Inputs are expected to be validated by :mod:`app.schemas.matching`. Integer
    arithmetic avoids floating-point error and Python's bankers rounding.
    """
    pace = pace_points(request_pace, candidate_pace)
    if pace is None:
        return None

    request_count = len(request_themes)
    if request_count == 0:
        raise ValueError("request_themes must not be empty.")
    overlap = len(set(request_themes).intersection(candidate_themes))

    # floor(100 * (0.8 * overlap/request_count + 0.2 * pace_fraction) + 0.5)
    # pace is already 20 (same) or 10 (adjacent), so this is
    # floor(80*overlap/request_count + pace + 0.5), represented exactly.
    numerator = 80 * overlap + pace * request_count
    return (2 * numerator + request_count) // (2 * request_count)
