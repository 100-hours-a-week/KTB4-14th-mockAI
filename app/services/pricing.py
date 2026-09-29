"""블로그 검색으로 가격 참고 정보를 붙입니다. 실패해도 생성은 계속되고, 모르는 가격은 null입니다."""
from __future__ import annotations

import asyncio

from app.core.exceptions import ApiError, InvalidModelOutput
from app.core.logging import record
from app.schemas.itinerary import ItineraryResponse


# 카테고리별 블로그 검색 키워드 (장소명 뒤에 붙습니다)
PRICE_KEYWORDS = {"관광": "입장료", "식당": "가격 메뉴", "숙소": "숙박 가격"}
SNIPPETS_PER_PLACE = 3
PRICE_TIMEOUT_SECONDS = 60


async def attach_prices(search_client, planner, itinerary: ItineraryResponse) -> ItineraryResponse:
    places = {item.provider_place_id: item for day in itinerary.days for item in day.items}
    if not places:
        return itinerary
    try:
        async with asyncio.timeout(PRICE_TIMEOUT_SECONDS):
            snippets = await asyncio.gather(*(
                search_client.blog_snippets(f"{item.place_name} {PRICE_KEYWORDS[item.category]}", SNIPPETS_PER_PLACE)
                for item in places.values()
            ))
            entries = [
                {"provider_place_id": pid, "place_name": item.place_name, "category": item.category, "snippets": found}
                for (pid, item), found in zip(places.items(), snippets)
            ]
            extracted = await planner.extract_prices(entries)
    except (ApiError, InvalidModelOutput, TimeoutError) as exc:
        record("price_lookup", places=len(places), found=0, failed=True, error_type=type(exc).__name__)
        return itinerary
    prices = {p.provider_place_id: p.price_info for p in extracted.prices if p.price_info}
    record("price_lookup", places=len(places), found=len(prices), failed=False)
    days = [
        day.model_copy(update={"items": [item.model_copy(update={"price_info": prices.get(item.provider_place_id)}) for item in day.items]})
        for day in itinerary.days
    ]
    return itinerary.model_copy(update={"days": days})
