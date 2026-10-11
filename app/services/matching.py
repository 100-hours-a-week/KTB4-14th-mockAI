"""Deterministic matching recommendation service."""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.matching import (
    MatchingCandidate,
    MatchingCard,
    MatchingConditions,
    MatchingResponse,
)
from app.services.matching_scoring import match_rate

RANKING_VERSION = "theme-pace-v1"


@dataclass(frozen=True)
class MatchingRecommendation:
    """Internal recommendation outcome, including non-HTTP observability data."""

    response: MatchingResponse
    filtered_count: int


def recommend(
    *,
    request_conditions: MatchingConditions,
    candidates: list[MatchingCandidate],
    limit: int = 3,
) -> MatchingRecommendation:
    """Filter, score, and rank candidates supplied by the backend.

    Budget fields are intentionally validation-only in this ranking version.
    """
    ranked: list[tuple[int, str, int, MatchingCandidate]] = []

    for candidate in candidates:
        if candidate.user_id == request_conditions.user_id:
            continue
        if (
            request_conditions.preferred_companion_gender != "ANY"
            and candidate.gender != request_conditions.preferred_companion_gender
        ):
            continue
        rate = match_rate(
            request_conditions.theme,
            candidate.theme,
            request_conditions.pace,
            candidate.pace,
        )
        if rate is not None:
            ranked.append((rate, candidate.nickname, candidate.user_id, candidate))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = ranked[:limit]
    cards = [
        MatchingCard(
            user_id=candidate.user_id,
            match_rate=rate,
        )
        for rate, _, _, candidate in selected
    ]
    return MatchingRecommendation(
        response=MatchingResponse(candidates=cards, count=len(cards)),
        filtered_count=len(ranked),
    )
