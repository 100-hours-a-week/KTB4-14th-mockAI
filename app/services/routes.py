"""실제 경로로 최종 시간표를 확정하고 응답용 일정을 만듭니다."""
from __future__ import annotations

import asyncio

from app.clients.kakao_routes import summarize_route
from app.core.exceptions import GenerationFailed, InvalidModelOutput
from app.core.geo import same_place
from app.core.logging import record
from app.schemas.itinerary import CATEGORY_ITEM_TYPE, ItineraryDay, ItineraryItem, ItineraryResponse, Place
from app.schemas.route import RouteDetails
from app.services.scheduler import WALK_LIMIT_KM, DayPlan, Visit, closed_meals, schedule_day


HOTEL_END_TIME = "23:59"


def day_points(visits: list[Visit], previous_hotel, hotel) -> list:
    return [p for p in [previous_hotel, *(v.place for v in visits), hotel] if p is not None]


async def fetch_routes(router, plans: list[DayPlan], visits: list[list[Visit]], hotels: list[Place | None]) -> dict:
    """(출발지, 도착지, 이동수단) 조합마다 길찾기를 한 번만 호출합니다."""
    requests = {}
    for plan, day, hotel in zip(plans, visits, hotels):
        previous = hotels[plan.index - 1] if plan.index else None
        points = day_points(day, previous, hotel)
        for origin, destination in zip(points, points[1:]):
            if not same_place(origin, destination):
                key = (origin.provider_place_id, destination.provider_place_id, plan.transport)
                requests.setdefault(key, (origin, destination, plan.soft_start, plan.transport))
    keys = list(requests)
    results = await asyncio.gather(*(router.route(*requests[key]) for key in keys))
    return dict(zip(keys, results))


def to_item(place: Place, sequence: int, start: str, end: str, meal_type, route: RouteDetails | None) -> ItineraryItem:
    return ItineraryItem(
        provider=place.provider, provider_place_id=place.provider_place_id, place_name=place.place_name,
        address=place.address or place.road_address or place.place_name, road_address=place.road_address,
        latitude=place.latitude, longitude=place.longitude, category=place.category, sequence=sequence,
        item_type=CATEGORY_ITEM_TYPE[place.category], meal_type=meal_type, start_time=start, end_time=end,
        route_from_previous=summarize_route(route) if route is not None else None,
    )


async def build_itinerary(
    router, travel_plan_id: int, title: str, plans: list[DayPlan], visits: list[list[Visit]], hotels: list[Place | None],
) -> ItineraryResponse:
    routes = await fetch_routes(router, plans, visits, hotels)

    def route(origin, destination, transport) -> RouteDetails | None:
        if origin is None or same_place(origin, destination):
            return None
        return routes[(origin.provider_place_id, destination.provider_place_id, transport)]

    days = []
    for plan, day, hotel in zip(plans, visits, hotels):
        previous_hotel = hotels[plan.index - 1] if plan.index else None
        incoming, origin = [], previous_hotel
        for visit in day:
            incoming.append(route(origin, visit.place, plan.transport))
            origin = visit.place
        to_hotel = route(origin, hotel, plan.transport) if hotel is not None else None
        transfers = [r.duration_minutes if r else 0 for r in incoming]
        try:
            schedule = schedule_day(plan, day, transfers, to_hotel.duration_minutes if to_hotel else 0)
        except InvalidModelOutput as exc:
            record("route_schedule_failed", day_number=plan.day_number, shortage_minutes=getattr(exc, "minutes", None))
            raise GenerationFailed("이동시간 안에 일정을 배치할 수 없습니다.", reason="routes_do_not_fit") from exc
        for closed in closed_meals(schedule):
            record("restaurant_hours_conflict", day_number=plan.day_number, place=closed.place.place_name,
                   at=closed.start.strftime("%H:%M"), hours=f"{closed.place.open_time}~{closed.place.close_time}",
                   closed_days=closed.place.closed_days)
        if plan.transport == "WALK":
            walked = sum(r.distance_meter for r in [*incoming, to_hotel] if r) / 1000
            if walked > WALK_LIMIT_KM:
                record("walking_limit_exceeded", day_number=plan.day_number, km=round(walked, 1))
                raise GenerationFailed("하루 도보 이동이 너무 깁니다.", reason="walking_limit")
        items = [
            to_item(timed.place, sequence, timed.start.strftime("%H:%M"), timed.end.strftime("%H:%M"), timed.meal_type, r)
            for sequence, (timed, r) in enumerate(zip(schedule.visits, incoming), 1)
        ]
        if hotel is not None:
            items.append(to_item(hotel, len(items) + 1, schedule.hotel_arrival.strftime("%H:%M"), HOTEL_END_TIME, None, to_hotel))
        days.append(ItineraryDay(day_number=plan.day_number, travel_date=plan.date, items=items))
    return ItineraryResponse(travel_plan_id=travel_plan_id, title=title, days=days)
