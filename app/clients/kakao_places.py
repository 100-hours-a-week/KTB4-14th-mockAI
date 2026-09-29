"""카카오 로컬 검색: 관광·식당 후보와 숙소."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import re
import time
import unicodedata

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import GenerationFailed, ServiceUnavailable
from app.core.logging import record
from app.schemas.itinerary import Place
from app.schemas.route import Coordinate


KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/{endpoint}.json"
THEME_QUERIES = {
    "NATURE": ("자연 관광", "AT4"),
    "FOOD": ("전통시장", ""),
    "CULTURE": ("박물관", "CT1"),
    "HISTORY": ("역사", "AT4"),
    "ACTIVITY": ("체험", ""),
    "HEALING": ("공원", "AT4"),
    "REST": ("공원", "AT4"),
    "SHOPPING": ("쇼핑", ""),
    "PHOTO": ("전망대", "AT4"),
    "SNS": ("전망대", "AT4"),
}
FOOD_QUERIES = {
    "KOREAN": "한식", "JAPANESE": "일식", "CHINESE": "중식", "WESTERN": "양식",
    "SEAFOOD": "해산물", "VEGETARIAN": "채식", "VEGAN": "비건", "ASIAN": "아시아음식",
}
GROUPS = {"AT4": "관광", "CT1": "관광", "FD6": "식당", "CE7": "식당", "AD5": "숙소"}
# (기본 반경 m, distance_preference 100일 때 추가 반경 m)
CANDIDATE_RADIUS = {"WALK": (1500, 3500), "PUBLIC_TRANSPORT": (4000, 8000), "CAR": (6000, 14000)}
CANDIDATE_LIMITS = {"관광": 60, "식당": 60}
REGION_ALIASES = {
    "제주특별자치도": "제주", "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
    "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
    "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북",
    "경상남도": "경남", "충청북도": "충북", "충청남도": "충남",
}


def category_of(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    if value in GROUPS:
        return GROUPS[value]
    upper = value.upper()
    if any(word in upper for word in ("숙박", "숙소", "호텔", "펜션", "STAY", "ACCOMMODATION")):
        return "숙소"
    if any(word in upper for word in ("음식", "식당", "카페", "RESTAURANT", "FOOD")):
        return "식당"
    if any(word in upper for word in ("관광", "문화", "여행", "명소", "시장", "쇼핑", "체험", "레저", "TOUR", "ATTRACTION", "ACTIVITY")):
        return "관광"
    return None


def region_tokens(value: str) -> set[str]:
    return {
        REGION_ALIASES.get(token, token)
        for token in re.split(r"\s+", unicodedata.normalize("NFC", value).strip()) if token
    }


def in_region(region: str, address: str) -> bool:
    return isinstance(address, str) and region_tokens(region).issubset(region_tokens(address))


@dataclass
class PlaceSearch:
    region_name: str
    transport: str
    distance_preference: int
    themes: list[str] = field(default_factory=list)
    foods: list[str] = field(default_factory=list)
    anchors: list = field(default_factory=list)


def to_place(doc: dict, category: str) -> Place:
    return Place(
        provider_place_id=str(doc["id"]),
        place_name=doc["place_name"],
        address=doc["address_name"],
        road_address=doc.get("road_address_name", ""),
        latitude=float(doc["y"]),
        longitude=float(doc["x"]),
        category=category,
        source_category=doc.get("category_name") or category,
    )


class KakaoPlaces:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.semaphore = asyncio.Semaphore(4)
        self._canonical_regions: dict[str, str] = {}

    async def _get(self, endpoint: str, params: dict) -> list[dict]:
        started = time.monotonic()
        if not self.settings.kakao_rest_api_key:
            raise ServiceUnavailable()
        try:
            async with self.semaphore:
                response = await self.client.get(
                    KAKAO_LOCAL_URL.format(endpoint=endpoint),
                    params=params,
                    headers={"Authorization": f"KakaoAK {self.settings.kakao_rest_api_key}"},
                    timeout=self.settings.kakao_timeout_seconds,
                )
            response.raise_for_status()
            documents = response.json()["documents"]
            if not isinstance(documents, list) or not all(isinstance(d, dict) for d in documents):
                raise ValueError("invalid documents")
            return documents
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            record("place_provider_failed", provider="kakao", endpoint=endpoint,
                   http_status=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
                   error_type=type(exc).__name__, elapsed_ms=round((time.monotonic() - started) * 1000))
            raise ServiceUnavailable() from exc

    async def region_center(self, region_name: str) -> tuple[str, Coordinate]:
        documents = await self._get("address", {"query": region_name})
        # 지역 별칭은 카카오의 행정구역 주소로 확정합니다. 시·군·구 접미사를 떼어 내면
        # 검색 범위가 모르게 넓어질 수 있으므로 그렇게 하지 않습니다.
        regions = {d.get("address_name"): d for d in documents
                   if d.get("address_type") == "REGION" and d.get("address_name")}
        if len(regions) > 1:
            raise GenerationFailed("여행 지역이 모호합니다.", reason="ambiguous_region")
        region = region_name
        if regions:
            region, document = next(iter(regions.items()))
            documents = [document]
        if not documents:
            documents = await self._get("keyword", {"query": region_name, "size": 1})
        if not documents:
            raise GenerationFailed("여행 지역을 찾을 수 없습니다.", reason="region_not_found")
        try:
            x, y = float(documents[0]["x"]), float(documents[0]["y"])
            if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
                raise ValueError("invalid center")
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceUnavailable() from exc
        if len(self._canonical_regions) >= 128:
            self._canonical_regions.pop(next(iter(self._canonical_regions)))
        self._canonical_regions[region_name] = region
        return region, Coordinate(latitude=y, longitude=x)

    async def collect(self, search: PlaceSearch) -> tuple[list[Place], Coordinate]:
        """필수 장소(없으면 지역 중심) 주변으로 모은 관광·식당 후보."""
        region, center = await self.region_center(search.region_name)
        base, spread = CANDIDATE_RADIUS[search.transport]
        radius = round(base + spread * search.distance_preference / 100)
        anchors = [(p.longitude, p.latitude) for p in search.anchors][:3] or [(center.longitude, center.latitude)]
        queries = []
        for theme in (search.themes or ["NATURE"])[:5]:
            query, group = THEME_QUERIES.get(theme, (theme, ""))
            queries.append((query, group, 1))
        for food in search.foods[:5]:
            queries.append((FOOD_QUERIES.get(food, food), "FD6", 1))
        # 후보 수를 자를 때 취향(테마·음식) 검색 결과가 먼저 남도록 앞에 둡니다.
        queries += [("관광명소", "AT4", 3), ("음식점", "FD6", 3)]
        calls = []
        for query, group, pages in dict.fromkeys(queries):
            for anchor_x, anchor_y in anchors:
                for page in range(1, pages + 1):
                    params = {"x": anchor_x, "y": anchor_y, "radius": radius,
                              "query": f"{region} {query}", "size": 15, "page": page}
                    if group:
                        params["category_group_code"] = group
                    calls.append(self._get("keyword", params))
        pool: dict[str, Place] = {}
        counts = dict.fromkeys(CANDIDATE_LIMITS, 0)
        stats = {"received": 0, "outside_region": 0, "unsupported_category": 0, "invalid_document": 0}

        def add(batches):
            for batch in batches:
                for doc in batch:
                    stats["received"] += 1
                    category = GROUPS.get(doc.get("category_group_code", "")) or category_of(doc.get("category_name", ""))
                    if category not in CANDIDATE_LIMITS:
                        stats["unsupported_category"] += 1
                        continue
                    if not in_region(region, doc.get("address_name", "")):
                        stats["outside_region"] += 1
                        continue
                    try:
                        place = to_place(doc, category)
                    except (ValidationError, KeyError, TypeError, ValueError):
                        stats["invalid_document"] += 1
                        continue
                    if place.provider_place_id not in pool and counts[category] < CANDIDATE_LIMITS[category]:
                        pool[place.provider_place_id] = place
                        counts[category] += 1

        add(await asyncio.gather(*calls))
        # 키워드 검색이 비어도 카테고리 검색에는 장소가 있을 수 있어 한 번 더 찾습니다.
        missing = [group for category, group in (("관광", "AT4"), ("식당", "FD6")) if not counts[category]]
        if missing:
            add(await asyncio.gather(*(self._get("category", {
                "category_group_code": group, "x": ax, "y": ay, "radius": radius, "sort": "distance", "size": 15,
            }) for group in missing for ax, ay in anchors)))
        record("place_collection", canonical_region=region, radius=radius, anchors=len(anchors),
               fallback_groups=missing, accepted=counts, **stats)
        if not pool:
            raise GenerationFailed("추천할 장소가 없습니다.", reason="no_place_candidates")
        return list(pool.values()), center

    async def accommodations(self, region_name: str, center, radius: int) -> list[Place]:
        documents = await self._get("category", {
            "category_group_code": "AD5", "x": center.longitude, "y": center.latitude,
            "radius": min(radius, 20000), "sort": "distance", "size": 15,
        })
        region = self._canonical_regions.get(region_name, region_name)
        inside, outside = {}, {}
        for doc in documents:
            if doc.get("category_group_code") != "AD5":
                continue
            try:
                place = to_place(doc, "숙소")
            except (ValidationError, KeyError, TypeError, ValueError):
                continue
            (inside if in_region(region, place.address) else outside)[place.provider_place_id] = place
        if not inside and outside:
            # 지역 경계 근처는 경계 밖 숙소만 있을 수 있습니다. 실패하는 것보다 가까운 숙소가 낫습니다.
            record("accommodation_region_fallback", canonical_region=region, candidates=len(outside))
            return list(outside.values())
        return list(inside.values())
