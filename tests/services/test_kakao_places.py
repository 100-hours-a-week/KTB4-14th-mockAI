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
