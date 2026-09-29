"""LLM 입출력 형식. 출력 모델은 OpenAI에 strict JSON Schema로 전달되므로
모든 필드가 필수입니다 (기본값 대신 null 허용)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictModel


class DayOverride(StrictModel):
    day_number: int = Field(ge=1, le=8)
    transport_type: Literal["WALK", "PUBLIC_TRANSPORT", "CAR"] | None
    pace_type: Literal["RELAXED", "BALANCED", "PACKED"] | None


class CustomRequest(StrictModel):
    """preference.extra_request를 구조화한 결과. 공통 필드(preference)를 덮어씁니다."""

    day_overrides: list[DayOverride] = Field(max_length=8)
    start_time: str | None = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    return_time: str | None = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    themes: list[str] = Field(max_length=5)
    foods: list[str] = Field(max_length=5)
    avoid: list[str] = Field(max_length=10)
    notes: str | None = Field(max_length=500)

    @classmethod
    def empty(cls) -> "CustomRequest":
        return cls(day_overrides=[], start_time=None, return_time=None, themes=[], foods=[], avoid=[], notes=None)


class SelectionItem(StrictModel):
    provider_place_id: str
    meal_type: Literal["BREAKFAST", "LUNCH", "DINNER"] | None


class SelectionDay(StrictModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    items: list[SelectionItem] = Field(max_length=12)


class ModelSelection(StrictModel):
    title: str = Field(min_length=1, max_length=50)
    days: list[SelectionDay] = Field(min_length=1, max_length=8)


class PlacePrice(StrictModel):
    provider_place_id: str
    price_info: str | None = Field(max_length=100)


class PriceExtraction(StrictModel):
    prices: list[PlacePrice]
