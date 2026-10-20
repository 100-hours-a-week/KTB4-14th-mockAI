"""Typed request and response contracts for deterministic matching scores."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from app.schemas.common import StrictModel


Theme = Literal["NATURE", "FOOD", "CULTURE", "REST", "SNS", "ACTIVITY"]
Pace = Literal["RELAXED", "BALANCED", "PACKED"]
Gender = Literal["FEMALE", "MALE"]
PreferredCompanionGender = Literal["ANY", "FEMALE", "MALE"]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
MatchingLimit = Annotated[StrictInt, Field(ge=1, le=3)]
BudgetInt = Annotated[StrictInt, Field(ge=0, le=3_000_000)]


class MatchingConditions(StrictModel):
    """Conditions supplied by the backend for a single matching request."""

    matching_request_id: PositiveInt
    user_id: PositiveInt
    preferred_companion_gender: PreferredCompanionGender
    theme: list[Theme] = Field(min_length=1, max_length=3)
    pace: Pace
    budget_min: BudgetInt | None = None
    budget_max: BudgetInt | None = None

    @field_validator("theme")
    @classmethod
    def unique_themes(cls, themes: list[Theme]) -> list[Theme]:
        if len(set(themes)) != len(themes):
            raise ValueError("theme must not contain duplicates.")
        return themes

    @model_validator(mode="after")
    def validate_budget_range(self) -> "MatchingConditions":
        if (self.budget_min is None) != (self.budget_max is None):
            raise ValueError("budget_min and budget_max must both be provided or both be null.")
        if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
            raise ValueError("budget_min must not exceed budget_max.")
        return self


class MatchingCandidate(StrictModel):
    """The minimal candidate profile supplied by the backend."""

    # Candidate profile data must be returned verbatim, except invalid blank names.
    model_config = ConfigDict(str_strip_whitespace=False)

    user_id: PositiveInt
    nickname: str = Field(min_length=1, max_length=500)
    profile_image_url: str | None = None
    gender: Gender
    theme: list[Theme] = Field(min_length=1, max_length=4)
    pace: Pace

    @field_validator("nickname")
    @classmethod
    def reject_blank_nickname(cls, nickname: str) -> str:
        if not nickname.strip():
            raise ValueError("nickname must not be blank.")
        return nickname

    @field_validator("theme")
    @classmethod
    def unique_themes(cls, themes: list[Theme]) -> list[Theme]:
        if len(set(themes)) != len(themes):
            raise ValueError("theme must not contain duplicates.")
        return themes


class MatchingRequest(StrictModel):
    request: MatchingConditions
    candidates: list[MatchingCandidate]
    limit: MatchingLimit = 3

    @model_validator(mode="after")
    def unique_candidate_ids(self) -> "MatchingRequest":
        candidate_ids = [candidate.user_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidates must not contain duplicate user_id values.")
        return self


class MatchingCard(StrictModel):
    """A matching score result for a backend-owned user profile."""

    user_id: PositiveInt
    match_rate: int = Field(ge=0, le=100)


class MatchingResponse(StrictModel):
    candidates: list[MatchingCard]
    count: int = Field(ge=0)

    @model_validator(mode="after")
    def count_matches_candidates(self) -> "MatchingResponse":
        if self.count != len(self.candidates):
            raise ValueError("count must equal the number of candidates.")
        return self
