from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
import unicodedata

from pydantic import Field, field_validator, model_validator

from app.schemas.common import KST, StrictModel
from app.schemas.route import RouteSummary


PaceType = Literal["RELAXED", "BALANCED", "PACKED"]
TransportType = Literal["WALK", "PUBLIC_TRANSPORT", "CAR"]
MealType = Literal["BREAKFAST", "LUNCH", "DINNER"]
Category = Literal["관광", "식당", "숙소"]
Weekday = Literal["월", "화", "수", "목", "금", "토", "일"]
WEEKDAYS = "월화수목금토일"  # date.weekday() 순서

PACE_ALIASES = {
    "RELAXED": "RELAXED", "여유롭게": "RELAXED",
    "BALANCED": "BALANCED", "보통": "BALANCED",
    "PACKED": "PACKED", "DENSE": "PACKED", "TIGHT": "PACKED", "BUSY": "PACKED", "빽빽하게": "PACKED",
}
TRANSPORT_ALIASES = {
    "WALK": "WALK", "WALKING": "WALK", "도보": "WALK",
    "PUBLIC_TRANSPORT": "PUBLIC_TRANSPORT", "PUBLIC_TRANSIT": "PUBLIC_TRANSPORT", "대중교통": "PUBLIC_TRANSPORT",
    "CAR": "CAR", "RENTAL_CAR": "CAR", "TAXI": "CAR", "자가용": "CAR", "렌터카": "CAR",
}
PLACE_TYPE_CATEGORY = {"TOURISM": "관광", "RESTAURANT": "식당", "ACCOMMODATION": "숙소"}
CATEGORY_ITEM_TYPE = {"관광": "TOUR", "식당": "RESTAURANT", "숙소": "ACCOMMODATION"}
NonEmpty = Field(min_length=1, max_length=500)


class Preference(StrictModel):
    pace_type: str = Field(min_length=1)
    transport_type: str = Field(min_length=1)
    budget_min: int | None = Field(default=None, ge=0)
    budget_max: int | None = Field(default=None, ge=0)
    budget_type: str = Field(pattern=r"^[A-Z]{3}$")
    distance_preference: int = Field(ge=0, le=100)
    themes: list[str] = Field(max_length=3)
    foods: list[str] = Field(default_factory=list, max_length=10)
    extra_request: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def normalize(self):
        if self.pace_type not in PACE_ALIASES:
            raise ValueError("pace_type: RELAXED, BALANCED, PACKED 중 하나여야 합니다.")
        if self.transport_type not in TRANSPORT_ALIASES:
            raise ValueError("transport_type: WALK, PUBLIC_TRANSPORT, CAR 중 하나여야 합니다.")
        self.pace_type = PACE_ALIASES[self.pace_type]
        self.transport_type = TRANSPORT_ALIASES[self.transport_type]
        if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
            raise ValueError("budget_max: budget_min 이상이어야 합니다.")
        return self


class RequiredPlace(StrictModel):
    provider: Literal["KAKAO"]
    provider_place_id: str = Field(min_length=1, max_length=500)
    place_name: str | None = Field(default=None, min_length=1, max_length=500)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    place_type: Literal["TOURISM", "RESTAURANT", "ACCOMMODATION"] = "TOURISM"
    order: int | None = Field(default=None, ge=1)


class TravelGenerationRequest(StrictModel):
    """백엔드(feature-travel)의 AiTravelGenerationRequest. 받는 요청 형식은 이것 하나입니다."""

    travel_plan_id: int = Field(gt=0)
    region_id: int = Field(gt=0)
    region_name: str = Field(min_length=1, max_length=500)
    arrival_datetime: datetime = Field(description="시간대 표기가 없으면 한국시간", examples=["2026-09-19T10:00:00"])
    departure_datetime: datetime = Field(description="시간대 표기가 없으면 한국시간", examples=["2026-09-21T18:00:00"])
    headcount: int = Field(ge=1, le=30)
    companion_type: str = Field(min_length=1, max_length=500)
    preference: Preference
    required_places: list[RequiredPlace] = Field(default_factory=list, max_length=64)

    @field_validator("region_name", mode="before")
    @classmethod
    def normalize_region(cls, value):
        # 조합 방식만 다른 같은 한글은 같은 검색 결과가 나오도록 NFC로 맞춥니다.
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value

    @field_validator("arrival_datetime", "departure_datetime", mode="before")
    @classmethod
    def require_datetime(cls, value):
        if not isinstance(value, datetime) and not (isinstance(value, str) and "T" in value):
            raise ValueError("날짜와 시각을 함께 적어야 합니다 (예: 2026-09-19T10:00:00).")
        return value

    @model_validator(mode="after")
    def validate_trip(self):
        if bool(self.arrival_datetime.tzinfo) != bool(self.departure_datetime.tzinfo):
            raise ValueError("departure_datetime: arrival_datetime과 시간대 표기가 같아야 합니다 (둘 다 +09:00을 붙이거나 둘 다 빼세요).")
        if self.departure_datetime <= self.arrival_datetime:
            raise ValueError("departure_datetime: arrival_datetime보다 늦어야 합니다.")
        start, end = self.local_bounds()
        if (end.date() - start.date()).days > 7:
            raise ValueError("departure_datetime: 여행 기간은 도착일 포함 최대 8일입니다.")
        ids = [p.provider_place_id for p in self.required_places]
        orders = [p.order if p.order is not None else i for i, p in enumerate(self.required_places, 1)]
        if len(set(ids)) != len(ids):
            raise ValueError("required_places: provider_place_id가 중복됐습니다.")
        if len(set(orders)) != len(orders):
            raise ValueError("required_places: order가 중복됐습니다.")
        return self

    def local_bounds(self) -> tuple[datetime, datetime]:
        def local(value: datetime) -> datetime:
            return value.astimezone(KST).replace(tzinfo=None) if value.tzinfo else value

        return local(self.arrival_datetime), local(self.departure_datetime)


class Place(StrictModel):
    """내부 후보 장소. 카카오 메타데이터는 장소 선택에만 씁니다."""

    provider: Literal["KAKAO"] = "KAKAO"
    provider_place_id: str = Field(min_length=1)
    place_name: str = Field(min_length=1)
    address: str = ""
    road_address: str = ""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: Category
    source_category: str = ""
    is_required: bool = False
    required_order: int | None = None
    # 식당 후보만: 카카오 공식 검색에서 확인한 영업시간(HH:MM), 정기 휴무 요일, 대표 메뉴. 모르면 None/빈 배열
    open_time: str | None = None
    close_time: str | None = None
    closed_days: list[str] = Field(default_factory=list)
    menus: list[str] = Field(default_factory=list)

    def is_closed_on(self, day) -> bool:
        return WEEKDAYS[day.weekday()] in self.closed_days

    def is_open_between(self, start, end) -> bool | None:
        """start~end(datetime)에 영업 중인지. 휴무 요일이면 False, 영업시간을 모르면 None입니다."""
        if self.is_closed_on(start):
            return False
        if not self.open_time or not self.close_time:
            return None
        day = start.date()
        opens = datetime.combine(day, time.fromisoformat(self.open_time))
        closes = datetime.combine(day, time.fromisoformat(self.close_time))
        if closes <= opens:
            closes += timedelta(days=1)  # 새벽까지 영업 (예: 17:00~02:00)
        return opens <= start and end <= closes


class ItineraryItem(StrictModel):
    provider: Literal["KAKAO"] = "KAKAO"
    provider_place_id: str
    place_name: str
    address: str
    road_address: str = ""
    latitude: float
    longitude: float
    category: Category
    sequence: int = Field(ge=1)
    item_type: Literal["TOUR", "RESTAURANT", "ACCOMMODATION"]
    meal_type: MealType | None = None
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="숙소는 23:59")
    price_info: str | None = Field(
        default=None,
        description="블로그 검색을 참고한 성인 1명 기준 가격. 관광 \"성인 19,000원\"(무료 \"성인 무료\"), 식당 \"1인분 평균 11,000원\", 숙소 \"1박 120,000원\". 가격을 알 수 없으면 \"확인 필요\"",
    )
    route_from_previous: RouteSummary | None = Field(
        default=None, description="이전 항목(다음날 첫 항목은 전날 숙소)에서 오는 이동. 여행 첫 항목은 null",
    )


class ItineraryDay(StrictModel):
    day_number: int = Field(ge=1)
    travel_date: date
    items: list[ItineraryItem]


class ItineraryResponse(StrictModel):
    travel_plan_id: int
    title: str = Field(min_length=1, max_length=50)
    days: list[ItineraryDay]
