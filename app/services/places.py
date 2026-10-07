"""모든 단계가 같이 쓰는 후보 정리와 기준 좌표."""
from __future__ import annotations

from app.core.exceptions import GenerationFailed
from app.core.geo import distance_km
from app.core.logging import record
from app.schemas.itinerary import PLACE_TYPE_CATEGORY, Place, TravelGenerationRequest


# 아침 식사에 어울리는 식당. 모델에 보낼 후보를 고를 때 날짜 수만큼 먼저 넣습니다.
BREAKFAST_WORDS = ("해장", "국밥", "죽", "백반", "브런치", "베이커리", "김밥", "토스트", "아침")


def is_breakfast_friendly(place) -> bool:
    return place.category == "식당" and any(w in place.place_name or w in (place.source_category or "") for w in BREAKFAST_WORDS)


# 점심·저녁 끼니 자리에도 넣을 수 있는 관광지(시장). 장소명이나 카카오 카테고리명에 이 단어가 있으면 시장으로 봅니다.
MARKET_KEYWORD = "시장"
MARKET_MEALS = ("LUNCH", "DINNER")
MARKET_CANDIDATES_PER_DAY = 2   # 모델에 보낼 시장 후보 수 (하루 관광 1곳 + 끼니 1곳 정도)


def is_market(place) -> bool:
    return place.category == "관광" and (MARKET_KEYWORD in place.place_name or MARKET_KEYWORD in (place.source_category or ""))


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


def object_particle(word: str) -> str:
    last = word[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - ord("가")) % 28 else "를"
    return "을(를)"


async def requested_places(places_client, region_name: str, names: list[str], required: list[Place]) -> list[Place]:
    """요청문에 적힌 장소를 카카오에서 찾아 필수 장소로 만듭니다. 순서는 정하지 않습니다(required_order 없음).

    필수 장소이므로 하나라도 찾지 못하면 일정을 만들지 않습니다.
    """
    names = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    if not names:
        return []
    found = await places_client.find_places(region_name, names)
    known = {p.provider_place_id for p in required}
    result = []
    for name, place in zip(names, found):
        if place is None:
            record("requested_place_not_found", requested=len(names))
            raise GenerationFailed(
                f"요청사항의 장소 '{name}'{object_particle(name)} 찾지 못했습니다. 필수 장소에서 선택해주세요.",
                reason="requested_place_not_found",
            )
        if place.provider_place_id in known:
            continue
        known.add(place.provider_place_id)
        result.append(place.model_copy(update={"is_required": True, "required_order": None}))
    record("requested_places_resolved", requested=len(names), added=len(result))
    return result


def select_candidates(
    places: list[Place], required: list[Place], days: int, distance_preference: int
) -> list[Place]:
    """모델에 보낼 후보 수를 제한합니다. 검색 순서와 필수 장소와의 거리를 섞어 고릅니다."""
    result = [p for p in required if p.category != "숙소"]
    seen = {p.provider_place_id for p in result} | {p.provider_place_id for p in required}
    limits = {"관광": max(10, days * 6), "식당": max(8, days * 5)}
    anchors = [p for p in required if p.category != "숙소"]
    for category, limit in limits.items():
        candidates = [p for p in places if p.category == category and p.provider_place_id not in seen]
        if category == "관광":
            # 시장 제한(하루 1곳)은 그대로이고, 후보에 시장이 몰려 시장이 아닌 관광지가 모자라지 않게
            # 시장 후보는 MARKET_CANDIDATES_PER_DAY × 날짜 수까지만 넣습니다 (끼니로 쓸 시장 포함).
            markets = [p for p in candidates if is_market(p)][: days * MARKET_CANDIDATES_PER_DAY]
            candidates = [p for p in candidates if not is_market(p) or p in markets]
        if category == "식당":
            # 아침 자리를 채울 수 있도록 아침에 어울리는 식당을 날짜 수만큼 앞에 둡니다.
            morning = [p for p in candidates if is_breakfast_friendly(p)][:days]
            result.extend(morning)
            limit -= len(morning)
            candidates = [p for p in candidates if p not in morning]
        if anchors:
            nearest = sorted(candidates, key=lambda p: min(distance_km(p, r) for r in anchors))
            chosen = nearest[: round(limit * (100 - distance_preference) / 100)]
            chosen_ids = {p.provider_place_id for p in chosen}
            chosen += [p for p in candidates if p.provider_place_id not in chosen_ids][: limit - len(chosen)]
        else:
            chosen = candidates[:limit]
        result.extend(chosen)
    return result
