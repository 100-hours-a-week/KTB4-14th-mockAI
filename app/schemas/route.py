from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_serializer

from app.schemas.common import StrictModel


class Coordinate(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RouteStop(StrictModel):
    name: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    station_number: str | None = Field(default=None, min_length=1, max_length=32)


class RouteVehicle(StrictModel):
    name: str
    type: str


class RouteLeg(StrictModel):
    """내부에만 두는 경로 구간. 응답에는 RouteSummary만 공개합니다."""

    mode: Literal["WALK", "BUS", "SUBWAY", "EXPRESSBUS", "TRAIN", "AIRPLANE", "FERRY", "CAR"]
    duration_seconds: int = Field(ge=0)
    distance_meter: int = Field(ge=0)
    start: RouteStop
    end: RouteStop
    departure_datetime: datetime
    arrival_datetime: datetime
    route_name: str | None = None
    bus_number: str | None = None
    vehicles: list[RouteVehicle] = Field(default_factory=list)


class RouteDetails(StrictModel):
    transport_type: str
    total_fare_amount: int | None = Field(default=None, ge=0, strict=True)
    duration_minutes: int = Field(ge=0)
    distance_meter: int = Field(ge=0)
    is_estimated: bool = True
    legs: list[RouteLeg] = Field(default_factory=list)


class TransitStopSummary(StrictModel):
    name: str = Field(min_length=1)
    station_number: str | None = Field(default=None, min_length=1, max_length=32)
    vehicle_number: list[str] = Field(default_factory=list)


class TransitLegSummary(StrictModel):
    sequence: int = Field(ge=1)
    mode: Literal["WALK", "BUS", "SUBWAY", "TRAIN", "EXPRESSBUS", "AIRPLANE", "FERRY"]
    boarding_stop: TransitStopSummary
    alighting_stop: TransitStopSummary
    vehicle_number: list[str] = Field(default_factory=list, description="버스 번호 배열. 지하철·도보는 빈 배열")
    line_name: list[str] = Field(default_factory=list, description="지하철 노선 배열. 버스·도보는 빈 배열")
    duration_minute: int = Field(ge=0)
    distance_meter: int = Field(ge=0)


class RouteSummary(StrictModel):
    """응답에 공개하는 경로 정보. 상세 좌표는 길찾기 클라이언트 안에만 둡니다."""

    transport_type: str
    duration_minutes: int = Field(ge=0)
    distance_meter: int = Field(ge=0)
    total_fare_amount: int | None = Field(
        default=None, ge=0, strict=True,
        description="카카오 경로 전체 요금(원). 미확인 null, 도보 0, 자동차 null",
    )
    legs: list[TransitLegSummary] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_summary(self, handler):
        data = handler(self)
        if not data["legs"]:
            data.pop("legs")
        return data
