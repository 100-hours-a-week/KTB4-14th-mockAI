"""밤마다 숙소를 배치합니다.

그날과 다음날이 모두 자동차면 [그날 마지막 장소, 다음날 첫 장소]의 중간 지점 근처에서 새로 고릅니다.
그 밖에는 오가는 길이 모두 편도 1시간 이내면 전날 숙소를 유지하고, 아니면 중간 지점 근처에서 새로 고릅니다.
"""
from __future__ import annotations

from app.core.exceptions import GenerationFailed, InvalidModelOutput
from app.core.geo import travel_minutes
from app.core.logging import record
from app.schemas.itinerary import Place
from app.services.places import midpoint
from app.services.scheduler import (
    DayPlan, TimeShortage, Visit, check_walking, estimated_transfers, schedule_day, walking_km,
)


# 숙소 검색 반경(m). 그날·다음날 중 느린 이동수단 기준입니다.
SEARCH_RADIUS = {"WALK": 1500, "PUBLIC_TRANSPORT": 5000, "CAR": 10000}
RADIUS_WIDEN_FACTOR = 1.5           # 결과가 없으면 반경을 이만큼 넓혀 1번 더 찾습니다.
REUSE_LIMIT_MINUTES = 60            # 도보·대중교통: 전날 숙소를 다시 쓰는 편도 이동 한계
TRANSPORT_RANK = {"WALK": 0, "PUBLIC_TRANSPORT": 1, "CAR": 2}


def fits(plans: list[DayPlan], visits: list[list[Visit]], index: int, previous_hotel, hotel) -> None:
    """이 숙소로 그날과 다음날 일정이 모두 들어가지 않으면 예외를 냅니다."""
    for day, before, after in ((index, previous_hotel, hotel), (index + 1, hotel, None)):
        plan = plans[day]
        transfers, tail = estimated_transfers(plan, visits[day], before, after)
        schedule_day(plan, visits[day], transfers, tail)
        check_walking(plan, walking_km(plan, visits[day], before, after))


def rejection(hotel: Place, exc: InvalidModelOutput) -> dict:
    return {"provider_place_id": hotel.provider_place_id,
            "shortage_minutes": exc.minutes if isinstance(exc, TimeShortage) else None,
            "reason": "time" if isinstance(exc, TimeShortage) else "walking_limit"}


async def assign_accommodations(
    places_client, region_name: str, plans: list[DayPlan], visits: list[list[Visit]],
    required_hotels: list[Place], region_center,
) -> list[Place | None]:
    hotels: list[Place | None] = [None] * len(plans)
    queue = list(required_hotels)
    for index, plan in enumerate(plans):
        if not plan.needs_hotel:
            continue
        tomorrow = plans[index + 1]
        previous = hotels[index - 1] if index else None
        last = visits[index][-1].place if visits[index] else previous
        first_next = visits[index + 1][0].place if visits[index + 1] else last
        last = last or first_next or region_center
        first_next = first_next or last

        if queue:
            hotel = queue.pop(0)
            try:
                fits(plans, visits, index, previous, hotel)
            except InvalidModelOutput as exc:
                record("accommodation_unavailable", day_number=plan.day_number, required=True, rejections=[rejection(hotel, exc)])
                raise GenerationFailed("지정한 숙소로 일정을 배치할 수 없습니다.", reason="required_hotel_unfit") from exc
            hotels[index] = hotel
            record("accommodation_selected", day_number=plan.day_number, required=True, reused=False)
            continue

        both_car = plan.transport == "CAR" and tomorrow.transport == "CAR"
        if previous is not None and not both_car:
            to_hotel = travel_minutes(last, previous, plan.transport)
            from_hotel = travel_minutes(previous, first_next, tomorrow.transport)
            if to_hotel <= REUSE_LIMIT_MINUTES and from_hotel <= REUSE_LIMIT_MINUTES:
                try:
                    fits(plans, visits, index, previous, previous)
                    hotels[index] = previous
                    record("accommodation_selected", day_number=plan.day_number, reused=True,
                           transfer_in_minutes=to_hotel, transfer_out_minutes=from_hotel)
                    continue
                except InvalidModelOutput:
                    pass

        center = midpoint(last, first_next)
        mode = min(plan.transport, tomorrow.transport, key=TRANSPORT_RANK.get)

        def cost(hotel: Place) -> int:
            return travel_minutes(last, hotel, plan.transport) + travel_minutes(hotel, first_next, tomorrow.transport)

        rejections, tried = [], set()
        for attempt, radius in enumerate((SEARCH_RADIUS[mode], round(SEARCH_RADIUS[mode] * RADIUS_WIDEN_FACTOR))):
            candidates = [h for h in await places_client.accommodations(region_name, center, radius)
                          if h.provider_place_id not in tried]
            for hotel in sorted(candidates, key=cost):
                tried.add(hotel.provider_place_id)
                try:
                    fits(plans, visits, index, previous, hotel)
                except InvalidModelOutput as exc:
                    rejections.append(rejection(hotel, exc))
                    continue
                hotels[index] = hotel
                record("accommodation_selected", day_number=plan.day_number, reused=False, radius=radius,
                       widened=attempt > 0, center=[round(center.latitude, 5), round(center.longitude, 5)],
                       transfer_in_minutes=travel_minutes(last, hotel, plan.transport),
                       transfer_out_minutes=travel_minutes(hotel, first_next, tomorrow.transport))
                break
            if hotels[index] is not None:
                break
        if hotels[index] is None:
            record("accommodation_unavailable", day_number=plan.day_number, radius=radius,
                   candidates=len(tried), rejections=rejections[:15])
            raise GenerationFailed("숙소를 찾지 못했습니다.", reason="accommodation_unavailable")
    return hotels
