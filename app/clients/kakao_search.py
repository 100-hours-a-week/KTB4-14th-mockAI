"""카카오(다음) 공식 검색 API. 가격·영업시간·메뉴 참고용으로 블로그·웹문서·카페 결과를 함께 씁니다.

https://developers.kakao.com/docs/latest/ko/daum-search/dev-guide
"""
from __future__ import annotations

import asyncio
import html
import re

import httpx

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable


KAKAO_SEARCH_URL = "https://dapi.kakao.com/v2/search/{source}"
# 참고 정보 검색에 쓰는 대상
SEARCH_SOURCES = ("blog", "web", "cafe")
TAG = re.compile(r"<[^>]+>")


class KakaoSearch:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.semaphore = asyncio.Semaphore(10)  # 동시 검색 수

    async def _search(self, source: str, query: str, size: int) -> list[str]:
        async with self.semaphore:
            response = await self.client.get(
                KAKAO_SEARCH_URL.format(source=source),
                params={"query": query, "size": size, "sort": "accuracy"},
                headers={"Authorization": f"KakaoAK {self.settings.kakao_rest_api_key}"},
                timeout=self.settings.kakao_timeout_seconds,
            )
        response.raise_for_status()
        return [html.unescape(TAG.sub("", d.get("contents", ""))).strip()
                for d in response.json()["documents"] if isinstance(d, dict)]

    async def search_snippets(self, query: str, size: int = 4) -> list[str]:
        """출처마다 size개씩 검색한 요약문. 일부 출처가 실패해도 나머지 결과를 씁니다."""
        if not self.settings.kakao_rest_api_key:
            raise ServiceUnavailable()
        results = await asyncio.gather(*(self._search(s, query, size) for s in SEARCH_SOURCES), return_exceptions=True)
        failures = (httpx.HTTPError, ValueError, KeyError, TypeError)
        if all(isinstance(r, failures) for r in results):
            raise ServiceUnavailable() from results[0]
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, failures):
                raise result
        return [snippet for result in results if isinstance(result, list) for snippet in result if snippet]
