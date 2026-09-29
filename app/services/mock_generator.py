"""/mock 경로: 실제 파이프라인에 가짜 클라이언트를 끼우고, 실패 시나리오를 재현합니다."""
from __future__ import annotations

import asyncio
import math
import re

from app.clients.mock import MockPlaces, MockRoutes, MockSearch
from app.core.exceptions import GenerationFailed
from app.core.geo import distance_km
from app.schemas.itinerary import TravelGenerationRequest
from app.schemas.planner import CustomRequest, ModelSelection, PlacePrice, PriceExtraction
from app.services.pipeline import Providers, generation_stages


FAIL_STAGES = {"fail_place": "PLACES", "fail_stay": "ACCOMMODATIONS", "fail_route": "ROUTES"}
PRICE = re.compile(r"[\d,]+원")


class MockPlanner:
    """LLM 대신 쓰는 목업 플래너. 항상 같은 결과를 내고 시간 규칙을 지킵니다."""

    async def parse_custom_request(self, context: dict) -> CustomRequest:
        return CustomRequest.empty()

    async def select_places(self, context: dict, feedback: str | None) -> ModelSelection:
        candidates = context["candidates"]
        required_ids = context["required_order"]
        by_id = {c["provider_place_id"]: c for c in candidates}
        used = set(required_ids)
        required = [by_id[pid] for pid in required_ids]
        plans = context["day_plans"]

        # 필수 장소는 순서대로, 자리가 남은 가장 이른 날에 넣습니다.
        assigned = [{"관광": [], "식당": []} for _ in plans]
        day = 0
        for place in required:
            while day < len(plans):
                room = plans[day]["tour_max"] if place["category"] == "관광" else len(plans[day]["meals"])
                if len(assigned[day][place["category"]]) < room:
                    assigned[day][place["category"]].append(place)
                    break
                day += 1

        def nearest(category: str, anchor, count: int) -> list[dict]:
            pool = [c for c in candidates if c["category"] == category and c["provider_place_id"] not in used]
            chosen = []
            for _ in range(count):
                if not pool:
                    break
                best = min(pool, key=lambda c: distance_km(_point(c), _point(anchor)) if anchor else 0)
                pool.remove(best)
                used.add(best["provider_place_id"])
                chosen.append(best)
                anchor = best
            return chosen

        days, anchor = [], None
        for plan, fixed in zip(plans, assigned):
            tours = fixed["관광"] + nearest("관광", anchor or (fixed["관광"] or [None])[0], max(0, plan["tour_min"] - len(fixed["관광"])))
            meals = fixed["식당"] + nearest("식당", (tours or [anchor])[0], len(plan["meals"]) - len(fixed["식당"]))
            items = _order(plan["meals"], tours, meals)
            anchor = by_id.get(items[-1]["provider_place_id"]) if items else anchor
            days.append({"date": plan["date"], "items": items})
        return ModelSelection(title=f"{context['request']['region']} 목업 여행", days=days)

    async def extract_prices(self, entries: list[dict]) -> PriceExtraction:
        prices = []
        for entry in entries:
            found = next((m.group() for s in entry["snippets"] for m in [PRICE.search(s)] if m), None)
            prices.append(PlacePrice(provider_place_id=entry["provider_place_id"], price_info=f"목업 {found}" if found else None))
        return PriceExtraction(prices=prices)


class _Point:
    def __init__(self, latitude: float, longitude: float):
        self.latitude, self.longitude = latitude, longitude


def _point(candidate: dict) -> _Point:
    return _Point(candidate["latitude"], candidate["longitude"])


def _order(meal_types: list[str], tours: list[dict], meals: list[dict]) -> list[dict]:
    """아침을 맨 앞에 두고, 관광지를 점심 전·점심과 저녁 사이·저녁 후에 나눠 배치합니다."""
    by_meal = dict(zip(meal_types, meals))
    before = math.ceil(len(tours) * 0.4) if "LUNCH" in by_meal else 0
    middle = len(tours) - before if "DINNER" not in by_meal else max(0, len(tours) - before - (1 if len(tours) >= 5 else 0))
    groups = [tours[:before], tours[before:before + middle], tours[before + middle:]]
    if "LUNCH" not in by_meal and "DINNER" not in by_meal:
        groups = [tours, [], []]

    def tour(place):
        return {"provider_place_id": place["provider_place_id"], "meal_type": None}

    def meal(kind):
        return [{"provider_place_id": by_meal[kind]["provider_place_id"], "meal_type": kind}] if kind in by_meal else []

    return (meal("BREAKFAST") + [tour(p) for p in groups[0]] + meal("LUNCH")
            + [tour(p) for p in groups[1]] + meal("DINNER") + [tour(p) for p in groups[2]])


def mock_providers(request: TravelGenerationRequest) -> Providers:
    return Providers(places=MockPlaces(request.travel_plan_id), routes=MockRoutes(), search=MockSearch(), planner=MockPlanner())


async def mock_stages(request: TravelGenerationRequest, scenario: str, delay_seconds: float):
    fail_stage = FAIL_STAGES.get(scenario)
    async for stage, status, result in generation_stages(request, mock_providers(request)):
        yield stage, status, result
        if status == "STARTED":
            await asyncio.sleep(delay_seconds)
            if stage == fail_stage:
                raise GenerationFailed("목업 실패 시나리오입니다.", reason=scenario)
            if scenario == "drop" and stage == "ROUTES":
                return
