"""끼니 자리에 들어간 시장을 그 시장 안 식당으로 바꿉니다. 응답에는 시장이 아니라 식당 이름이 나옵니다.

1순위: 카카오 로컬 키워드 검색("시장명 맛집", 시장 반경 안 음식점, 정확도순)의 첫 번째 가게
2순위: 1순위 결과가 없으면 시장에서 가장 가까운 음식점
둘 다 없거나 이미 일정에 있는 가게뿐이면 시장 항목을 그대로 둡니다.
"""
from __future__ import annotations

from app.core.logging import record
from app.services.places import is_market
from app.services.scheduler import Visit


async def resolve_market_meals(places_client, visits: list[list[Visit]]) -> list[list[Visit]]:
    used = {v.place.provider_place_id for day in visits for v in day}
    result = []
    for day in visits:
        resolved = []
        for visit in day:
            if visit.meal_type and is_market(visit.place):
                stores = await places_client.market_restaurants(visit.place)
                store = next((s for s in stores if s.provider_place_id not in used), None)
                record("market_meal", market=visit.place.place_name, resolved=store is not None, candidates=len(stores))
                if store is not None:
                    used.add(store.provider_place_id)
                    visit = Visit(store, visit.meal_type)
            resolved.append(visit)
        result.append(resolved)
    return result
