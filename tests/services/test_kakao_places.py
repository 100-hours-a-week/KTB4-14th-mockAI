import asyncio

import httpx

from app.clients.kakao_places import KakaoPlaces
from app.core.config import Settings
from tests.conftest import place


DOC = {"id": "77", "place_name": "시장 앞 칼국수", "address_name": "부산광역시 테스트로 1",
       "x": "129.06", "y": "35.16", "category_group_code": "FD6", "category_name": "음식점 > 한식"}


def test_market_store_falls_back_to_the_nearest_restaurant_when_no_top_pick():
    calls = []

    def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        calls.append((endpoint, request.url.params.get("sort")))
        return httpx.Response(200, json={"documents": [] if endpoint == "keyword.json" else [DOC]})

    places = KakaoPlaces(httpx.AsyncClient(transport=httpx.MockTransport(handler)), Settings(kakao_rest_api_key="test"))
    stores = asyncio.run(places.market_restaurants(place("m", "관광")))

    assert calls == [("keyword.json", "accuracy"), ("category.json", "distance")]
    assert [s.place_name for s in stores] == ["시장 앞 칼국수"]
    assert stores[0].category == "식당"


def test_find_places_picks_the_first_supported_result_inside_the_region():
    region = {"address_name": "부산광역시", "address_type": "REGION", "x": "129.07", "y": "35.17"}
    outside = {**DOC, "id": "1", "place_name": "서울 해운대", "address_name": "서울특별시 중구"}
    unsupported = {**DOC, "id": "2", "category_group_code": "PK6", "category_name": "교통 > 주차장"}
    beach = {**DOC, "id": "3", "place_name": "해운대해수욕장", "category_group_code": "AT4", "category_name": "여행 > 해수욕장"}
    queries = []

    def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "address.json":
            return httpx.Response(200, json={"documents": [region]})
        queries.append(request.url.params["query"])
        found = [outside, unsupported, beach] if "해운대" in request.url.params["query"] else []
        return httpx.Response(200, json={"documents": found})

    places = KakaoPlaces(httpx.AsyncClient(transport=httpx.MockTransport(handler)), Settings(kakao_rest_api_key="test"))
    found = asyncio.run(places.find_places("부산광역시", ["해운대", "없는곳"]))

    assert queries == ["부산광역시 해운대", "부산광역시 없는곳"]
    assert [(p.provider_place_id, p.category) for p in found if p] == [("3", "관광")]
    assert found[1] is None
