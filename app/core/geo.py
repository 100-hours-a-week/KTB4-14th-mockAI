"""clients와 services가 같이 쓰는 좌표 기반 추정. 길찾기 API 결과가 아닙니다."""
from __future__ import annotations

import math


# 직선 거리에 이 값을 곱해 실제 이동 거리를 추정합니다.
DETOUR_FACTOR = 1.4
# 이동수단별 (속도 km/h, 고정 추가 시간 분). 넉넉하게 잡은 추정치입니다.
TRAVEL_SPEEDS = {"WALK": (4, 0), "PUBLIC_TRANSPORT": (18, 15), "CAR": (30, 10)}


def distance_km(first, second) -> float:
    lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(second.longitude - first.longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))


def path_km(first, second) -> float:
    return distance_km(first, second) * DETOUR_FACTOR


def same_place(first, second) -> bool:
    first_id = getattr(first, "provider_place_id", None)
    return first_id is not None and first_id == getattr(second, "provider_place_id", None)


def travel_minutes(first, second, transport: str) -> int:
    if same_place(first, second):
        return 0
    speed, overhead = TRAVEL_SPEEDS[transport]
    return max(5, math.ceil(path_km(first, second) / speed * 60 + overhead))
