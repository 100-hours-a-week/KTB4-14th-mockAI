"""카카오(다음) 블로그 검색. 가격 참고용으로만 씁니다."""
from __future__ import annotations

import asyncio
import html
import re

import httpx

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable


KAKAO_BLOG_URL = "https://dapi.kakao.com/v2/search/blog"
TAG = re.compile(r"<[^>]+>")


class KakaoSearch:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.semaphore = asyncio.Semaphore(4)

    async def blog_snippets(self, query: str, size: int = 3) -> list[str]:
        if not self.settings.kakao_rest_api_key:
            raise ServiceUnavailable()
        try:
            async with self.semaphore:
                response = await self.client.get(
                    KAKAO_BLOG_URL,
                    params={"query": query, "size": size, "sort": "accuracy"},
                    headers={"Authorization": f"KakaoAK {self.settings.kakao_rest_api_key}"},
                    timeout=self.settings.kakao_timeout_seconds,
                )
            response.raise_for_status()
            documents = response.json()["documents"]
            return [html.unescape(TAG.sub("", d.get("contents", ""))).strip() for d in documents if isinstance(d, dict)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ServiceUnavailable() from exc
