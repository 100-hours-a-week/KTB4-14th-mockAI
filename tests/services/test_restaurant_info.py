import asyncio

from app.core.exceptions import ServiceUnavailable
from app.schemas.planner import RestaurantInfo, RestaurantInfoExtraction
from app.services.market_meals import resolve_market_meals
from app.services.restaurant_info import enrich_restaurants
from app.services.scheduler import Visit
from tests.conftest import place


class Search:
    def __init__(self, fail=False):
        self.queries, self.fail = [], fail

    async def search_snippets(self, query, size=4):
        if self.fail:
            raise ServiceUnavailable()
        self.queries.append(query)
        return [query]


class Planner:
    async def extract_restaurant_info(self, entries):
        return RestaurantInfoExtraction(restaurants=[
            RestaurantInfo(provider_place_id="r1", open_time="07:00", close_time="21:00", closed_days=["월", "월"], menus=["국밥"]),
            RestaurantInfo(provider_place_id="r2", open_time="11:00", close_time=None, closed_days=[], menus=[]),
        ])


def test_restaurants_get_hours_and_menus_but_tours_are_not_searched():
    search = Search()
    pool = [place("t1", "관광"), place("r1", "식당"), place("r2", "식당")]

    result = asyncio.run(enrich_restaurants(search, Planner(), pool))

    assert search.queries == ["장소 r1 영업시간 휴무일 메뉴", "장소 r2 영업시간 휴무일 메뉴"]
    by_id = {p.provider_place_id: p for p in result}
    assert (by_id["r1"].open_time, by_id["r1"].close_time, by_id["r1"].menus) == ("07:00", "21:00", ["국밥"])
    assert by_id["r1"].closed_days == ["월"]
    # 시작·마감 중 하나만 있으면 영업 여부를 판단할 수 없어 둘 다 비웁니다.
    assert (by_id["r2"].open_time, by_id["r2"].close_time) == (None, None)


def test_search_failure_keeps_candidates_unchanged():
    pool = [place("r1", "식당")]

    assert asyncio.run(enrich_restaurants(Search(fail=True), Planner(), pool)) == pool


class Stores:
    def __init__(self, stores):
        self.stores = stores

    async def market_restaurants(self, market):
        return self.stores


def test_market_meal_becomes_the_top_unused_store():
    market = place("m", "관광", source_category="쇼핑 > 전통시장").model_copy(update={"place_name": "광장시장"})
    used = place("s1", "식당")
    visits = [[Visit(used, "BREAKFAST"), Visit(market, "LUNCH")]]

    result = asyncio.run(resolve_market_meals(Stores([place("s1", "식당"), place("s2", "식당")]), visits))

    lunch = result[0][1]
    assert lunch.place.provider_place_id == "s2"
    assert lunch.place.place_name == "장소 s2"
    assert lunch.meal_type == "LUNCH"


def test_market_meal_stays_when_no_store_is_found():
    market = place("m", "관광", source_category="쇼핑 > 전통시장")
    visits = [[Visit(market, "DINNER")]]

    assert asyncio.run(resolve_market_meals(Stores([]), visits))[0][0].place.provider_place_id == "m"


def test_extraction_is_split_into_concurrent_batches():
    batches = []

    class Batching:
        async def extract_restaurant_info(self, entries):
            batches.append(len(entries))
            return RestaurantInfoExtraction(restaurants=[
                RestaurantInfo(provider_place_id=e["provider_place_id"], open_time="09:00", close_time="21:00",
                               closed_days=[], menus=[]) for e in entries])

    pool = [place(f"r{i}", "식당") for i in range(12)]
    result = asyncio.run(enrich_restaurants(Search(), Batching(), pool))

    assert batches == [5, 5, 2]
    assert all(p.open_time == "09:00" for p in result)
