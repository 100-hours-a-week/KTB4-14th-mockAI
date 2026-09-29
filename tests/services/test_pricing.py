import asyncio

from app.core.exceptions import ServiceUnavailable
from app.schemas.itinerary import ItineraryDay, ItineraryItem, ItineraryResponse
from app.schemas.planner import PlacePrice, PriceExtraction
from app.services.pricing import attach_prices


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

    async def blog_snippets(self, query, size=3):
        if self.fail:
            raise ServiceUnavailable()
        self.queries.append(query)
        return [f"{query} 후기"]


class Planner:
    async def extract_prices(self, entries):
        return PriceExtraction(prices=[PlacePrice(provider_place_id="a", price_info="입장료 3,000원"),
                                       PlacePrice(provider_place_id="b", price_info=None)])


def test_every_place_is_searched_and_unknown_prices_stay_null():
    search = Search()

    result = asyncio.run(attach_prices(search, Planner(), itinerary()))

    assert search.queries == ["a 입장료", "b 가격 메뉴"]
    assert [i.price_info for i in result.days[0].items] == ["입장료 3,000원", None]


def test_search_failure_keeps_the_itinerary():
    result = asyncio.run(attach_prices(Search(fail=True), Planner(), itinerary()))

    assert [i.price_info for i in result.days[0].items] == [None, None]
