"""모든 단계가 같이 쓰는 후보 정리와 기준 좌표."""
from __future__ import annotations

from app.core.exceptions import GenerationFailed
from app.core.geo import distance_km
from app.schemas.itinerary import PLACE_TYPE_CATEGORY, Place, TravelGenerationRequest


class Point:
    def __init__(self, latitude: float, longitude: float, name: str = ""):
        self.latitude, self.longitude, self.place_name = latitude, longitude, name
        self.provider_place_id = f"point:{latitude:.5f},{longitude:.5f}"


def midpoint(first, second) -> Point:
    return Point((first.latitude + second.latitude) / 2, (first.longitude + second.longitude) / 2)


def required_places(request: TravelGenerationRequest) -> list[Place]:
    places = []
    for index, required in enumerate(request.required_places, 1):
        if required.place_name is None or required.latitude is None or required.longitude is None:
            raise GenerationFailed("필수 장소의 이름·좌표가 없습니다.", reason="required_place_details_missing")
        places.append(Place(
            provider=required.provider,
            provider_place_id=required.provider_place_id,
            place_name=required.place_name,
            address=required.address or "",
            latitude=required.latitude,
            longitude=required.longitude,
            category=PLACE_TYPE_CATEGORY[required.place_type],
            source_category=required.place_type,
            is_required=True,
            required_order=required.order if required.order is not None else index,
        ))
    return sorted(places, key=lambda p: p.required_order)


def select_candidates(
    places: list[Place], required: list[Place], days: int, distance_preference: int
) -> list[Place]:
    """모델에 보낼 후보 수를 제한합니다. 검색 순서와 필수 장소와의 거리를 섞어 고릅니다."""
    result = [p for p in required if p.category != "숙소"]
    seen = {p.provider_place_id for p in result} | {p.provider_place_id for p in required}
    limits = {"관광": max(8, days * 4), "식당": max(6, days * 4)}
    anchors = [p for p in required if p.category != "숙소"]
    for category, limit in limits.items():
        candidates = [p for p in places if p.category == category and p.provider_place_id not in seen]
        if anchors:
            nearest = sorted(candidates, key=lambda p: min(distance_km(p, r) for r in anchors))
            chosen = nearest[: round(limit * (100 - distance_preference) / 100)]
            chosen_ids = {p.provider_place_id for p in chosen}
            chosen += [p for p in candidates if p.provider_place_id not in chosen_ids][: limit - len(chosen)]
        else:
            chosen = candidates[:limit]
        result.extend(chosen)
    return result
