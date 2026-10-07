"""/mock 경로용 가짜 카카오 클라이언트. 결과가 항상 같고 네트워크를 쓰지 않습니다.

실제 클라이언트와 같은 형태를 돌려주므로 실제 파이프라인·시간 계산·검증이 그대로 동작합니다.
"""
from __future__ import annotations

import math
import random
import zlib

from app.core.geo import path_km, travel_minutes
from app.schemas.itinerary import Place
from app.schemas.route import Coordinate, RouteDetails, RouteLeg, RouteStop, RouteVehicle


REGION_CENTERS = {
    "서귀포": (33.2541, 126.5601), "제주": (33.4996, 126.5312), "서울": (37.5665, 126.9780),
    "부산": (35.1796, 129.0756), "강릉": (37.7519, 128.8761), "속초": (38.2070, 128.5918),
    "경주": (35.8562, 129.2247), "여수": (34.7604, 127.6622), "전주": (35.8242, 127.1480),
    "인천": (37.4563, 126.7052), "대구": (35.8714, 128.6014), "광주": (35.1595, 126.8526),
    "대전": (36.3504, 127.3845),
}
DEFAULT_CENTER = REGION_CENTERS["서울"]
# 후보를 흩뿌릴 반경(km). 도보는 좁게 잡아 하루 도보 상한 안에 들어오게 합니다.
SPREAD_KM = {"WALK": 1.2, "PUBLIC_TRANSPORT": 3.0, "CAR": 6.0}
TOUR_KINDS = ["전망대", "해변 산책로", "공원", "박물관", "전통시장", "숲길", "미술관", "사찰", "야경 명소", "체험관"]
FOOD_KINDS = ["한식당", "국밥집", "해산물 식당", "분식집", "고깃집", "브런치 카페", "면요리집", "백반집"]
MORNING_FOODS = ("국밥", "백반", "브런치", "해장")
CANDIDATES_PER_CATEGORY = 40


def region_center(region_name: str) -> tuple[float, float]:
    for key, center in REGION_CENTERS.items():
        if key in region_name:
            return center
    return DEFAULT_CENTER


def scatter(rng: random.Random, lat: float, lng: float, max_km: float, min_km: float = 0.0) -> tuple[float, float]:
    distance = min_km + (max_km - min_km) * math.sqrt(rng.random())
    angle = rng.random() * 2 * math.pi
    dlat = distance * math.cos(angle) / 111.0
    dlng = distance * math.sin(angle) / (111.0 * math.cos(math.radians(lat)))
    return round(lat + dlat, 6), round(lng + dlng, 6)


class MockPlaces:
    def __init__(self, seed: int):
        self.seed = seed

    async def collect(self, search) -> tuple[list[Place], Coordinate]:
        lat, lng = region_center(search.region_name)
        rng = random.Random(self.seed)
        spread = SPREAD_KM[search.transport]
        # 실제 검색처럼 필수 장소(없으면 지역 중심) 주변에 후보를 흩어 놓습니다.
        anchors = [(p.latitude, p.longitude) for p in search.anchors][:3] or [(lat, lng)]
        pool = []
        for prefix, category, kinds in (("tour", "관광", TOUR_KINDS), ("food", "식당", FOOD_KINDS)):
            for n in range(1, CANDIDATES_PER_CATEGORY + 1):
                y, x = scatter(rng, *anchors[n % len(anchors)], spread)
                kind = kinds[(n - 1) % len(kinds)]
                pool.append(Place(
                    provider_place_id=f"mock-{prefix}-{n}", place_name=f"목업 {kind} {n}",
                    address=f"{search.region_name} 목업로 {n}", latitude=y, longitude=x,
                    category=category, source_category=kind,
                ))
        return pool, Coordinate(latitude=lat, longitude=lng)

    async def accommodations(self, region_name: str, center, radius: int) -> list[Place]:
        # 같은 중심 좌표면 같은 숙소가 나오므로 재사용·재검색 결과도 항상 같습니다.
        key = f"{center.latitude:.3f},{center.longitude:.3f}"
        rng = random.Random(zlib.crc32(f"{self.seed}:{key}".encode()))
        hotels = []
        for n in range(1, 6):
            y, x = scatter(rng, center.latitude, center.longitude, min(0.8, radius / 1000), 0.1)
            hotels.append(Place(
                provider_place_id=f"mock-stay-{key}-{n}", place_name=f"목업 호텔 {key[-3:]}-{n}",
                address=f"{region_name} 목업숙박로 {n}", latitude=y, longitude=x,
                category="숙소", source_category="호텔",
            ))
        return hotels


    async def market_restaurants(self, market: Place) -> list[Place]:
        rng = random.Random(zlib.crc32(f"{self.seed}:{market.provider_place_id}".encode()))
        stores = []
        for n in range(1, 4):
            y, x = scatter(rng, market.latitude, market.longitude, 0.2)
            stores.append(Place(
                provider_place_id=f"{market.provider_place_id}-store-{n}", place_name=f"{market.place_name} 목업 맛집 {n}",
                address=market.address, latitude=y, longitude=x, category="식당", source_category="음식점 > 한식",
            ))
        return stores


class MockRoutes:
    async def route(self, origin, destination, departure, transport: str) -> RouteDetails:
        minutes = travel_minutes(origin, destination, transport)
        meters = round(path_km(origin, destination) * 1000)
        start = RouteStop(name=getattr(origin, "place_name", "출발지"))
        end = RouteStop(name=getattr(destination, "place_name", "도착지"))
        legs, fare = [], None
        if transport == "PUBLIC_TRANSPORT":
            fare = 1500
            legs = [RouteLeg(mode="BUS", duration_seconds=minutes * 60, distance_meter=meters, start=start, end=end,
                             departure_datetime=departure, arrival_datetime=departure,
                             vehicles=[RouteVehicle(name="100", type="목업버스")])]
        elif transport == "WALK":
            legs = [RouteLeg(mode="WALK", duration_seconds=minutes * 60, distance_meter=meters, start=start, end=end,
                             departure_datetime=departure, arrival_datetime=departure)]
        return RouteDetails(transport_type=transport, total_fare_amount=fare, duration_minutes=minutes,
                            distance_meter=meters, is_estimated=True, legs=legs)


class MockSearch:
    async def search_snippets(self, query: str, size: int = 4) -> list[str]:
        if "영업시간" in query:
            # 아침 식사에 어울리는 가게는 07시, 나머지는 11시에 엽니다.
            early = any(word in query for word in MORNING_FOODS)
            menu = next((word for word in MORNING_FOODS if word in query), "정식")
            holiday = "월" if zlib.crc32(query.encode()) % 3 == 0 else None
            closed = f", 매주 {holiday}요일 휴무" if holiday else ", 연중무휴"
            return [f"{query} 후기. 영업시간 {'07:00' if early else '11:00'}~21:00{closed}, 대표 메뉴 {menu}"]
        price = (zlib.crc32(query.encode()) % 20 + 1) * 1000
        if "1박" in query:
            price *= 8
        return [f"{query} 다녀왔어요. 가격은 {price:,}원이었습니다."]
