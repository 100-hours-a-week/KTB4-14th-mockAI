"""생성 단계. 각 단계 이벤트를 먼저 내보낸 뒤 다음 단계를 실행합니다. 작업 저장소는 없습니다."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from app.clients.kakao_places import PlaceSearch
from app.core.logging import record, timed
from app.schemas.itinerary import TravelGenerationRequest
from app.services.accommodation import TRANSPORT_RANK, assign_accommodations
from app.services.market_meals import resolve_market_meals
from app.services.places import required_places, select_candidates
from app.services.planner import interpret_custom_request, recommend_places
from app.services.pricing import PriceTarget, lookup_prices, with_prices
from app.services.restaurant_info import enrich_restaurants
from app.services.routes import build_itinerary
from app.services.scheduler import build_day_plans, estimated_transfers, schedule_day, validate_generation_window


@dataclass
class Providers:
    """외부 의존성 묶음. 실제 경로는 카카오·OpenAI 클라이언트를, /mock은 가짜 클라이언트를 씁니다."""

    places: object
    routes: object
    search: object
    planner: object


async def generation_stages(request: TravelGenerationRequest, providers: Providers):
    required = required_places(request)
    yield "PLACES", "STARTED", None
    arrival, departure = request.local_bounds()
    dates = [(arrival.date() + timedelta(days=i)).isoformat() for i in range((departure.date() - arrival.date()).days + 1)]
    with timed("custom_request"):
        custom = await interpret_custom_request(providers.planner, request, dates)
    plans = build_day_plans(request, custom)
    validate_generation_window(plans, required)
    search = PlaceSearch(
        region_name=request.region_name,
        transport=min((p.transport for p in plans), key=TRANSPORT_RANK.get),
        distance_preference=request.preference.distance_preference,
        themes=request.preference.themes + custom.themes,
        foods=request.preference.foods + custom.foods,
        anchors=[p for p in required if p.category != "숙소"],
    )
    with timed("place_collection"):
        pool, center = await providers.places.collect(search)
    candidates = select_candidates(pool, required, len(plans), request.preference.distance_preference)
    with timed("restaurant_info"):
        candidates = await enrich_restaurants(providers.search, providers.planner, candidates)
    title, visits = await recommend_places(providers.planner, request, custom, plans, candidates, required)
    with timed("market_meals"):
        visits = await resolve_market_meals(providers.places, visits)
    for plan, day in zip(plans, visits):
        transfers, tail = estimated_transfers(plan, day, None, None)
        record("day_spare", day_number=plan.day_number, visits=len(day),
               spare_minutes=schedule_day(plan, day, transfers, tail).spare_minutes, hotel_reserve_minutes=tail)
    yield "PLACES", "COMPLETED", None

    yield "ACCOMMODATIONS", "STARTED", None
    with timed("accommodations"):
        hotels = await assign_accommodations(
            providers.places, request.region_name, plans, visits,
            [p for p in required if p.category == "숙소"], center,
        )
    yield "ACCOMMODATIONS", "COMPLETED", None

    yield "ROUTES", "STARTED", None
    # 가격은 시간표와 상관없어서 길찾기와 동시에 조회합니다.
    targets = [PriceTarget(v.place.provider_place_id, v.place.place_name, v.place.category, v.meal_type)
               for day in visits for v in day]
    targets += [PriceTarget(h.provider_place_id, h.place_name, h.category) for h in hotels if h is not None]
    source_categories = {v.place.provider_place_id: v.place.source_category for day in visits for v in day}
    source_categories |= {h.provider_place_id: h.source_category for h in hotels if h is not None}

    async def routed():
        with timed("routes"):
            return await build_itinerary(providers.routes, request.travel_plan_id, title, plans, visits, hotels)

    async def priced():
        with timed("prices"):
            return await lookup_prices(providers.search, providers.planner, targets, source_categories)

    itinerary, prices = await asyncio.gather(routed(), priced())
    itinerary = with_prices(itinerary, prices)
    yield "ROUTES", "COMPLETED", None
    yield "COMPLETE", "COMPLETED", itinerary


async def run_to_completion(stages) -> object:
    """모든 단계를 끝까지 실행하고 최종 일정을 돌려줍니다 (JSON API용)."""
    result = None
    async for stage, _, value in stages:
        if stage == "COMPLETE":
            result = value
    return result
