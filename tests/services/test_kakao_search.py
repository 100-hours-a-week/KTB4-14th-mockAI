import asyncio

import httpx
import pytest

from app.clients.kakao_search import KakaoSearch
from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable


def client(handler):
    return KakaoSearch(httpx.AsyncClient(transport=httpx.MockTransport(handler)), Settings(kakao_rest_api_key="test"))


def test_blog_web_and_cafe_results_are_combined_without_tags():
    seen = []

    def handler(request):
        source = request.url.path.rsplit("/", 1)[-1]
        seen.append((source, request.url.params["size"]))
        return httpx.Response(200, json={"documents": [{"contents": f"<b>{source}</b> 입장료 3,000원 &amp; 주차"}]})

    snippets = asyncio.run(client(handler).search_snippets("경복궁 입장료 성인", 4))

    assert sorted(seen) == [("blog", "4"), ("cafe", "4"), ("web", "4")]
    assert sorted(snippets) == ["blog 입장료 3,000원 & 주차", "cafe 입장료 3,000원 & 주차", "web 입장료 3,000원 & 주차"]


def test_one_failing_source_does_not_drop_the_others():
    def handler(request):
        if request.url.path.endswith("/cafe"):
            return httpx.Response(500)
        return httpx.Response(200, json={"documents": [{"contents": "1인 12,000원"}]})

    assert asyncio.run(client(handler).search_snippets("국밥")) == ["1인 12,000원", "1인 12,000원"]


def test_all_sources_failing_is_service_unavailable():
    with pytest.raises(ServiceUnavailable):
        asyncio.run(client(lambda request: httpx.Response(500)).search_snippets("국밥"))
