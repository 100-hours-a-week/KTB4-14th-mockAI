"""카카오 REST 길찾기. https://developers.kakao.com/docs/ko/kakaomap/rest-api#routing

출발 날짜 파라미터가 없어서 결과는 시간표가 아니라 경로 정보입니다.
카카오 REST에는 자동차 길찾기가 없어서 CAR는 좌표 기반 추정치를 씁니다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math

import httpx

from app.core.config import Settings
from app.core.exceptions import RoutingUnavailable
from app.core.geo import distance_km, path_km, travel_minutes
from app.schemas.route import RouteDetails, RouteLeg, RouteStop, RouteSummary, RouteVehicle, TransitLegSummary


KAKAO_ROUTING_URL = "https://dapi.kakao.com/v2/routing/"
NO_TRANSIT_STATUSES = {"NO_RESULTS", "STARTNODES_NULL", "ENDNODES_NULL", "EQUAL_POINTS"}


def summarize_route(details: RouteDetails) -> RouteSummary:
    # 공개할 안내만 골라 담습니다. 경로 좌표와 전체 정류장 목록은 내부에만 둡니다.
    legs = []
    if details.transport_type in {"PUBLIC_TRANSPORT", "WALK"}:
        for leg in details.legs:
            if leg.mode == "CAR":
                continue
            names = list(dict.fromkeys(" ".join(v.name.split()) for v in leg.vehicles if v.name.strip()))
            buses = names if leg.mode in {"BUS", "EXPRESSBUS"} else []
            if not buses and leg.mode in {"BUS", "EXPRESSBUS"} and leg.bus_number:
                buses = [leg.bus_number]
            lines = names if leg.mode in {"SUBWAY", "TRAIN"} else []
            if not lines and leg.mode in {"SUBWAY", "TRAIN"} and leg.route_name:
                lines = [leg.route_name]
            legs.append(TransitLegSummary(
                sequence=len(legs) + 1,
                mode=leg.mode,
                vehicle_number=buses,
                line_name=lines,
                boarding_stop={"name": leg.start.name or "출발지", "station_number": None, "vehicle_number": buses},
                alighting_stop={"name": leg.end.name or "도착지", "station_number": None, "vehicle_number": buses},
                duration_minute=math.ceil(leg.duration_seconds / 60),
                distance_meter=leg.distance_meter,
            ))
    fare = 0 if details.transport_type == "WALK" else details.total_fare_amount if details.transport_type == "PUBLIC_TRANSPORT" else None
    return RouteSummary(
        transport_type=details.transport_type,
        duration_minutes=details.duration_minutes,
        distance_meter=details.distance_meter,
        total_fare_amount=fare,
        legs=legs,
    )


def route_fare(properties: dict) -> int | None:
    """카카오가 명시한 전체 경로 요금(정수)만 받습니다. 범위 값은 쓰지 않습니다."""
    fare = properties.get("fare")
    value = fare.get("value") if isinstance(fare, dict) else None
    return value if type(value) is int and value >= 0 else None


def number(value) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid numeric value")
    value = float(value)
    if not math.isfinite(value) or value < 0 or not value.is_integer():
        raise ValueError("invalid numeric value")
    return int(value)


def endpoints(raw) -> tuple[RouteStop, RouteStop]:
    points = raw["path"]["points"]
    if len(points) < 2:
        raise ValueError("missing step path")
    return (RouteStop(name="", longitude=points[0][0], latitude=points[0][1]),
            RouteStop(name="", longitude=points[-1][0], latitude=points[-1][1]))


def named_point(place) -> RouteStop:
    return RouteStop(name=getattr(place, "place_name", ""), latitude=place.latitude, longitude=place.longitude)


def estimate(origin, destination, transport: str, departure: datetime) -> RouteDetails:
    minutes = travel_minutes(origin, destination, transport)
    return RouteDetails(
        transport_type=transport,
        is_estimated=True,
        duration_minutes=minutes,
        distance_meter=round(path_km(origin, destination) * 1000),
    )


class KakaoRoutes:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client, self.settings = client, settings

    async def _get(self, mode: str, origin, destination) -> dict:
        if not self.settings.kakao_rest_api_key:
            raise RoutingUnavailable()
        try:
            response = await self.client.get(
                KAKAO_ROUTING_URL + mode,
                headers={"Authorization": "KakaoAK " + self.settings.kakao_rest_api_key},
                params={
                    "start_x": origin.longitude, "start_y": origin.latitude,
                    "end_x": destination.longitude, "end_y": destination.latitude,
                    "s_name": named_point(origin).name, "e_name": named_point(destination).name,
                    "input_coord": "WGS84", "output_coord": "WGS84",
                },
                timeout=self.settings.routing_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "status" not in payload:
                raise ValueError("invalid routing response")
            return payload
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RoutingUnavailable() from exc

    async def _walk(self, origin, destination, departure: datetime) -> RouteDetails:
        payload = await self._get("walk", origin, destination)
        if payload["status"] == "SAME_POINT" and distance_km(origin, destination) < 0.002:
            return RouteDetails(transport_type="WALK", is_estimated=False, duration_minutes=0, distance_meter=0)
        if payload["status"] != "OK":
            raise RoutingUnavailable()
        properties = payload["route"]["properties"]
        seconds, distance = number(properties["totalTime"]), number(properties["totalDistance"])
        if distance > 0 and seconds == 0:
            raise ValueError("missing pedestrian duration")
        return RouteDetails(
            transport_type="WALK", is_estimated=False,
            duration_minutes=math.ceil(seconds / 60), distance_meter=distance,
            legs=[RouteLeg(
                mode="WALK", duration_seconds=seconds, distance_meter=distance,
                start=named_point(origin), end=named_point(destination),
                departure_datetime=departure, arrival_datetime=departure + timedelta(seconds=seconds),
            )],
        )

    async def _transit(self, payload: dict, origin, destination, departure: datetime) -> RouteDetails:
        options = payload["routes"]
        if not isinstance(options, list) or not options:
            raise ValueError("missing successful transit route")
        option = min(options, key=lambda r: number(r["properties"]["totalTime"]))
        if not option["steps"]:
            raise ValueError("missing transit steps")
        legs, cursor, previous = [], departure, named_point(origin)
        for raw in option["steps"]:
            props = raw["properties"]
            mode = {"WALKING": "WALK", "BUS": "BUS", "SUBWAY": "SUBWAY"}[props["type"]]
            distance, seconds = number(props["distance"]), number(props["time"])
            first, last = endpoints(raw)
            stops = [s["name"] for s in props.get("stops", [])]
            if mode != "WALK" and (len(stops) < 2 or not stops[0].strip() or not stops[-1].strip()):
                raise ValueError("missing transit boarding or alighting stop")
            start = first.model_copy(update={"name": stops[0] if stops else previous.name})
            end = last.model_copy(update={"name": stops[-1] if stops else "도보 도착 지점"})
            # 카카오가 도보 구간을 빼먹을 수 있어 승차 전·환승 도보를 따로 조회합니다.
            if distance_km(previous, start) > 0.01:
                access = await self._walk(previous, start, cursor)
                legs.extend(access.legs)
                cursor += timedelta(minutes=access.duration_minutes)
            vehicles = [RouteVehicle.model_validate(v) for v in props.get("vehicles", [])]
            if mode != "WALK" and not vehicles:
                raise ValueError("missing transit line")
            legs.append(RouteLeg(
                mode=mode, duration_seconds=seconds, distance_meter=distance, start=start, end=end,
                departure_datetime=cursor, arrival_datetime=cursor + timedelta(seconds=seconds),
                bus_number=vehicles[0].name if vehicles and mode == "BUS" else None,
                route_name=f"{vehicles[0].type} {vehicles[0].name}" if vehicles else None,
                vehicles=vehicles,
            ))
            cursor += timedelta(seconds=seconds)
            previous = end
        if not any(leg.mode in {"BUS", "SUBWAY"} for leg in legs):
            raise ValueError("no transit leg in successful transit response")
        if distance_km(previous, destination) > 0.01:
            legs.extend((await self._walk(previous, destination, cursor)).legs)
        # 대기 시간이 포함된 카카오 총시간과 조회한 도보 시간 합 중 큰 값을 씁니다.
        total = max(number(option["properties"]["totalTime"]), sum(leg.duration_seconds for leg in legs))
        return RouteDetails(
            transport_type="PUBLIC_TRANSPORT",
            total_fare_amount=route_fare(option["properties"]),
            is_estimated=False,
            duration_minutes=math.ceil(total / 60),
            distance_meter=max(number(option["properties"]["totalDistance"]), sum(leg.distance_meter for leg in legs)),
            legs=legs,
        )

    async def route(self, origin, destination, departure: datetime, transport: str) -> RouteDetails:
        try:
            if transport == "CAR":
                return estimate(origin, destination, "CAR", departure)
            if transport == "WALK":
                return await self._walk(origin, destination, departure)
            payload = await self._get("publictraffic", origin, destination)
            if payload["status"] in NO_TRANSIT_STATUSES:
                # 대중교통 경로가 없으면(가까운 거리 등) 도보로 대체합니다.
                return await self._walk(origin, destination, departure)
            if payload["status"] != "OK":
                raise RoutingUnavailable()
            return await self._transit(payload, origin, destination, departure)
        except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
            raise RoutingUnavailable() from exc
