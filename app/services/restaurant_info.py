"""식당 후보의 영업시간·정기 휴무 요일·대표 메뉴를 카카오 공식 검색(블로그·웹문서·카페)으로 채웁니다.

카카오 로컬 API에는 영업시간·메뉴가 없어서 검색 요약문에서 LLM이 뽑습니다. 요약문에 없으면 추측하지 않고 비워 둡니다.
실패해도 일정 생성은 계속되며, 영업시간을 모르는 식당은 제외하지 않고 허용합니다.
"""
from __future__ import annotations

import asyncio

from app.core.exceptions import ApiError, InvalidModelOutput
from app.core.logging import record
from app.schemas.itinerary import Place


INFO_KEYWORD = "영업시간 휴무일 메뉴"   # 장소명 뒤에 붙는 검색어
SNIPPETS_PER_SOURCE = 2          # 블로그·웹문서·카페 각각에서 가져올 결과 수
INFO_TIMEOUT_SECONDS = 30
EXTRACT_BATCH_SIZE = 5          # LLM 한 번에 보낼 식당 수. 나눈 묶음은 동시에 호출합니다.


async def enrich_restaurants(search_client, planner, candidates: list[Place]) -> list[Place]:
    restaurants = [c for c in candidates if c.category == "식당"]
    if not restaurants:
        return candidates
    try:
        async with asyncio.timeout(INFO_TIMEOUT_SECONDS):
            snippets = await asyncio.gather(*(
                search_client.search_snippets(f"{r.place_name} {INFO_KEYWORD}", SNIPPETS_PER_SOURCE) for r in restaurants
            ))
            entries = [
                {"provider_place_id": r.provider_place_id, "place_name": r.place_name, "address": r.address, "snippets": found}
                for r, found in zip(restaurants, snippets)
            ]
            batches = [entries[i:i + EXTRACT_BATCH_SIZE] for i in range(0, len(entries), EXTRACT_BATCH_SIZE)]
            results = await asyncio.gather(*(planner.extract_restaurant_info(batch) for batch in batches))
    except (ApiError, InvalidModelOutput, TimeoutError) as exc:
        record("restaurant_info", restaurants=len(restaurants), failed=True, error_type=type(exc).__name__)
        return candidates
    info = {}
    for found in (r for result in results for r in result.restaurants):
        # 시작·마감 중 하나만 있으면 영업 여부를 판단할 수 없어 둘 다 비웁니다.
        hours = (found.open_time, found.close_time) if found.open_time and found.close_time else (None, None)
        info[found.provider_place_id] = {"open_time": hours[0], "close_time": hours[1],
                                         "closed_days": list(dict.fromkeys(found.closed_days)), "menus": found.menus}
    record("restaurant_info", restaurants=len(restaurants), failed=False,
           with_hours=sum(1 for v in info.values() if v["open_time"]),
           with_closed_days=sum(1 for v in info.values() if v["closed_days"]),
           with_menus=sum(1 for v in info.values() if v["menus"]))
    return [c.model_copy(update=info[c.provider_place_id]) if c.provider_place_id in info else c for c in candidates]
