import asyncio

from app.core.exceptions import ServiceUnavailable
from app.schemas.itinerary import ItineraryDay, ItineraryItem, ItineraryResponse
from app.schemas.planner import PlacePrice, PriceExtraction
import pytest

from app.services.pricing import attach_prices, format_price


def itinerary():
    items = [
        ItineraryItem(provider_place_id=pid, place_name=pid, address="부산", latitude=35.1, longitude=129.0,
                      category=category, sequence=i, item_type=kind, start_time="10:00", end_time="11:00")
        for i, (pid, category, kind) in enumerate([("a", "관광", "TOUR"), ("b", "식당", "RESTAURANT")], 1)
    ]
    return ItineraryResponse(travel_plan_id=1, title="t", days=[ItineraryDay(day_number=1, travel_date="2026-09-19", items=items)])


class Search:
    def __init__(self, fail=False):
        self.queries, self.fail = [], fail

    async def search_snippets(self, query, size=4):
        if self.fail:
            raise ServiceUnavailable()
        self.queries.append(query)
        return [f"{query} 후기"]


class Planner:
    async def extract_prices(self, entries):
        return PriceExtraction(prices=[PlacePrice(provider_place_id="a", amount_won=3000),
                                       PlacePrice(provider_place_id="b", amount_won=None)])


def test_every_place_is_searched_and_unknown_prices_need_checking():
    search = Search()

    result = asyncio.run(attach_prices(search, Planner(), itinerary()))

    assert search.queries == ["a 입장료 성인", "b 메뉴 가격"]
    assert [i.price_info for i in result.days[0].items] == ["성인 3,000원", "확인 필요"]


def test_search_failure_keeps_the_itinerary():
    result = asyncio.run(attach_prices(Search(fail=True), Planner(), itinerary()))

    assert [i.price_info for i in result.days[0].items] == ["확인 필요", "확인 필요"]


@pytest.mark.parametrize("category, amount, expected", [
    ("관광", 19000, "성인 19,000원"),
    ("관광", 0, "성인 무료"),
    ("식당", 11400, "1인분 평균 11,000원"),
    ("식당", 11600, "1인분 평균 12,000원"),
    ("숙소", 118500, "1박 119,000원"),
    ("식당", 0, None),
    ("식당", 1_000_000, None),
    ("숙소", 5000, None),
    ("관광", None, None),
])
def test_prices_use_one_format_per_category(category, amount, expected):
    assert format_price(category, amount) == expected


def test_free_type_tourism_without_a_price_is_free_but_paid_types_need_checking():
    base = itinerary()
    items = base.days[0].items
    items = [items[0].model_copy(update={"provider_place_id": "m", "place_name": "못골종합시장"}),
             items[0].model_copy(update={"provider_place_id": "z", "place_name": "서울대공원 동물원"}),
             items[0].model_copy(update={"provider_place_id": "k", "place_name": "궁궐"})]
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": items})]})

    class NoPrices:
        async def extract_prices(self, entries):
            return PriceExtraction(prices=[PlacePrice(provider_place_id=e["provider_place_id"], amount_won=None) for e in entries])

    result = asyncio.run(attach_prices(Search(), NoPrices(), base, {"k": "여행 > 관광,명소 > 고궁"}))

    assert [i.price_info for i in result.days[0].items] == ["성인 무료", "확인 필요", "확인 필요"]


def test_found_price_wins_over_the_free_assumption():
    base = itinerary()
    park = base.days[0].items[0].model_copy(update={"provider_place_id": "a", "place_name": "어린이대공원"})
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": [park]})]})

    result = asyncio.run(attach_prices(Search(), Planner(), base))

    assert result.days[0].items[0].price_info == "성인 3,000원"


def test_market_visited_as_a_tour_is_always_free():
    base = itinerary()
    market = base.days[0].items[0].model_copy(update={"provider_place_id": "a", "place_name": "광장시장"})
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": [market]})]})

    result = asyncio.run(attach_prices(Search(), Planner(), base))

    assert result.days[0].items[0].price_info == "성인 무료"


def test_search_failure_still_marks_free_type_places_free():
    base = itinerary()
    park = base.days[0].items[0].model_copy(update={"place_name": "올림픽공원"})
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": [park]})]})

    result = asyncio.run(attach_prices(Search(fail=True), Planner(), base))

    assert result.days[0].items[0].price_info == "성인 무료"


def test_market_used_as_a_meal_is_priced_per_person():
    base = itinerary()
    market = base.days[0].items[0].model_copy(update={"provider_place_id": "a", "place_name": "광장시장", "meal_type": "LUNCH"})
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": [market]})]})
    search = Search()

    class MealPrice:
        async def extract_prices(self, entries):
            assert entries[0]["category"] == "식당"
            return PriceExtraction(prices=[PlacePrice(provider_place_id="a", amount_won=9500)])

    result = asyncio.run(attach_prices(search, MealPrice(), base))

    assert search.queries == ["광장시장 메뉴 가격"]
    assert result.days[0].items[0].price_info == "1인분 평균 10,000원"


def test_market_meal_without_a_price_is_not_assumed_free():
    base = itinerary()
    market = base.days[0].items[0].model_copy(update={"provider_place_id": "a", "place_name": "광장시장", "meal_type": "DINNER"})
    base = base.model_copy(update={"days": [base.days[0].model_copy(update={"items": [market]})]})

    result = asyncio.run(attach_prices(Search(fail=True), Planner(), base))

    assert result.days[0].items[0].price_info == "확인 필요"
