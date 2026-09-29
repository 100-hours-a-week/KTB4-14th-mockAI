"""카카오 공식 검색(블로그·웹문서·카페)으로 가격 참고 정보를 붙입니다. 실패해도 생성은 계속되고, 모르는 가격은 "확인 필요"입니다."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.exceptions import ApiError, InvalidModelOutput
from app.core.logging import record
from app.schemas.itinerary import ItineraryResponse
from app.services.places import MARKET_KEYWORD


# 카테고리별 검색 키워드 (장소명 뒤에 붙습니다). 끼니 자리의 시장은 "식당" 기준으로 찾습니다.
PRICE_KEYWORDS = {"관광": "입장료 성인", "식당": "메뉴 가격", "숙소": "1박 가격"}
# 가격을 알 수 없을 때(검색 결과에 없음, 범위 밖, 검색 실패) 반환하는 문구
UNKNOWN_PRICE = "확인 필요"
# 카테고리별 응답 문구, 반올림 단위(원), 허용 범위(원). 범위를 벗어나면 잘못 읽은 값으로 보고 UNKNOWN_PRICE입니다.
PRICE_FORMATS = {
    "관광": {"template": "성인 {amount}원", "free": "성인 무료", "round": 1, "range": (0, 300_000)},
    "식당": {"template": "1인분 평균 {amount}원", "free": None, "round": 1000, "range": (1_000, 300_000)},
    "숙소": {"template": "1박 {amount}원", "free": None, "round": 1000, "range": (10_000, 3_000_000)},
}
# 입장료 개념이 없는 관광지 유형. 가격을 못 찾아도 카카오 카테고리나 장소명에 있으면 "성인 무료"로 봅니다.
FREE_ADMISSION_KEYWORDS = ("시장", "공원", "해수욕장", "해변", "해안", "거리", "광장", "산책로", "둘레길",
                           "올레", "도보여행", "숲길", "골목", "방파제", "포구", "항구", "등산로", "계곡", "호수")
# 위 단어가 있어도 보통 입장료가 있는 유형
PAID_ADMISSION_KEYWORDS = ("놀이공원", "테마공원", "테마파크", "워터파크", "동물원", "식물원", "수목원", "아쿠아리움")
SNIPPETS_PER_SOURCE = 4  # 블로그·웹문서·카페 각각에서 가져올 결과 수
PRICE_TIMEOUT_SECONDS = 60
EXTRACT_BATCH_SIZE = 6          # LLM 한 번에 보낼 장소 수. 나눈 묶음은 동시에 호출합니다.


@dataclass(frozen=True)
class PriceTarget:
    """가격을 찾을 장소. 일정 항목(ItineraryItem)과 같은 속성 이름을 씁니다."""

    provider_place_id: str
    place_name: str
    category: str
    meal_type: str | None = None


async def attach_prices(
    search_client, planner, itinerary: ItineraryResponse, source_categories: dict[str, str] | None = None,
) -> ItineraryResponse:
    """완성된 일정에 가격을 붙입니다. source_categories는 장소 ID → 카카오 카테고리명(예: "쇼핑 > 전통시장")입니다."""
    targets = [PriceTarget(i.provider_place_id, i.place_name, i.category, i.meal_type) for d in itinerary.days for i in d.items]
    return with_prices(itinerary, await lookup_prices(search_client, planner, targets, source_categories))


async def lookup_prices(
    search_client, planner, targets: list[PriceTarget], source_categories: dict[str, str] | None = None,
) -> dict[str, str]:
    """장소 ID → 가격 문구. 일정 시간표와 상관없어서 길찾기와 동시에 실행할 수 있습니다."""
    source_categories = source_categories or {}
    places = {t.provider_place_id: t for t in targets}
    if not places:
        return {}
    try:
        async with asyncio.timeout(PRICE_TIMEOUT_SECONDS):
            snippets = await asyncio.gather(*(
                search_client.search_snippets(f"{item.place_name} {PRICE_KEYWORDS[price_kind(item)]}", SNIPPETS_PER_SOURCE)
                for item in places.values()
            ))
            entries = [
                {"provider_place_id": pid, "place_name": item.place_name, "category": price_kind(item),
                 "kakao_category": source_categories.get(pid, ""), "snippets": found}
                for (pid, item), found in zip(places.items(), snippets)
            ]
            batches = [entries[i:i + EXTRACT_BATCH_SIZE] for i in range(0, len(entries), EXTRACT_BATCH_SIZE)]
            results = await asyncio.gather(*(planner.extract_prices(batch) for batch in batches))
    except (ApiError, InvalidModelOutput, TimeoutError) as exc:
        record("price_lookup", places=len(places), found=0, failed=True, error_type=type(exc).__name__)
        return {**free_admissions(places, source_categories), **market_admissions(places, source_categories)}
    categories = {pid: price_kind(item) for pid, item in places.items()}
    prices = {}
    for price in (p for result in results for p in result.prices):
        text = format_price(categories.get(price.provider_place_id), price.amount_won)
        if text:
            prices[price.provider_place_id] = text
    found = len(prices)
    # 찾은 가격이 무료 추정보다 우선하지만, 관광지로 들어간 시장은 입장료가 없으므로 항상 무료입니다.
    prices = {**free_admissions(places, source_categories), **prices, **market_admissions(places, source_categories)}
    record("price_lookup", places=len(places), found=found, assumed_free=len(prices) - found, failed=False)
    return prices


def price_kind(item) -> str:
    """가격 기준 카테고리. 끼니 자리에 들어간 시장은 입장료가 아니라 1인분 가격을 찾습니다."""
    return "식당" if item.meal_type else item.category


def with_prices(itinerary: ItineraryResponse, prices: dict[str, str]) -> ItineraryResponse:
    days = [
        day.model_copy(update={"items": [
            item.model_copy(update={"price_info": prices.get(item.provider_place_id, UNKNOWN_PRICE)}) for item in day.items
        ]})
        for day in itinerary.days
    ]
    return itinerary.model_copy(update={"days": days})


def format_price(category: str | None, amount: int | None) -> str | None:
    """카테고리 기준에 맞춰 가격 문구를 만듭니다. 기준에 맞지 않으면 None입니다."""
    rule = PRICE_FORMATS.get(category)
    if rule is None or amount is None:
        return None
    low, high = rule["range"]
    if not low <= amount <= high:
        return None
    if amount == 0:
        return rule["free"]
    step = rule["round"]
    rounded = max(step, (amount + step // 2) // step * step)  # 사사오입 (118,500 → 119,000)
    return rule["template"].format(amount=f"{rounded:,}")


def is_free_admission(name: str, source_category: str) -> bool:
    text = f"{source_category} {name}"
    return any(word in text for word in FREE_ADMISSION_KEYWORDS) and not any(word in text for word in PAID_ADMISSION_KEYWORDS)


def free_admissions(places: dict, source_categories: dict[str, str]) -> dict[str, str]:
    """가격을 못 찾은 관광지 중 입장료가 없는 유형은 "성인 무료"로 채웁니다."""
    return {
        pid: PRICE_FORMATS["관광"]["free"]
        for pid, item in places.items()
        if price_kind(item) == "관광" and is_free_admission(item.place_name, source_categories.get(pid, ""))
    }


def market_admissions(places: dict, source_categories: dict[str, str]) -> dict[str, str]:
    """관광지로 들어간 시장은 상품·주차 가격을 입장료로 잘못 읽지 않도록 "성인 무료"로 고정합니다."""
    return {
        pid: PRICE_FORMATS["관광"]["free"]
        for pid, item in places.items()
        if price_kind(item) == "관광"
        and (MARKET_KEYWORD in item.place_name or MARKET_KEYWORD in source_categories.get(pid, ""))
    }
