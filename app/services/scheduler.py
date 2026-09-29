"""날짜별 활동 시간, 분 단위 시간 배정, 장소 선택 검증.

반드시 지키는 규칙: 00:00~07:00 휴식, 첫날은 도착 + 1시간부터, 마지막 날은 출발 − 1시간까지.
나머지(09:00 출발, 22~23시 귀가, 끼니 시각)는 권장값이라 하루에 다 들어가지 않으면 양보합니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math

from app.core.exceptions import GenerationFailed, InvalidModelOutput
from app.schemas.itinerary import WEEKDAYS, Place, TravelGenerationRequest
from app.schemas.planner import CustomRequest, ModelSelection
from app.core.geo import TRAVEL_SPEEDS, path_km, travel_minutes
from app.services.places import MARKET_MEALS, is_market


# ---------------------------------------------------------------------------
# 조정 가능한 일정 규칙
# ---------------------------------------------------------------------------
REST_END = time(7, 0)                 # 00:00~07:00은 무조건 휴식 (반드시 지킴)
LATEST_RETURN = time(23, 59)          # 숙소 도착 최종 한계 (반드시 지킴)
TARGET_START = time(9, 0)             # 숙소 출발 권장 시각
TARGET_RETURN = time(22, 30)          # 숙소 도착 권장 시각 (22~23시)
ARRIVAL_BUFFER_MINUTES = 60           # 도착 장소 → 첫 일정 이동 여유
DEPARTURE_BUFFER_MINUTES = 60         # 마지막 일정 → 출발 장소 이동 여유
FULL_DAY_MINUTES = 810                # TARGET_START~TARGET_RETURN, 관광 개수 비례 계산 기준
WALK_LIMIT_KM = 50                    # 도보인 날 하루 도보 이동 상한
MARKET_TOURS_PER_DAY = 1              # 하루에 관광지로 넣을 수 있는 시장 수 (끼니로 쓴 시장은 제외)
MAX_OPENING_WAIT_MINUTES = 90         # 식당이 이 시간 안에 문을 열면 오픈 시각까지 기다렸다가 식사합니다

# pace별 관광 개수 (하루를 다 쓰는 날 기준 최소, 최대)
TOUR_COUNTS = {"RELAXED": (2, 4), "BALANCED": (3, 5), "PACKED": (4, 6)}
# pace별 체류시간(분): 카테고리 → (최소, 최대). 여유가 있으면 중간값까지 늘립니다.
STAY_MINUTES = {
    "RELAXED": {"관광": (90, 150), "식당": (60, 90)},
    "BALANCED": {"관광": (60, 120), "식당": (45, 75)},
    "PACKED": {"관광": (45, 90), "식당": (40, 60)},
}
# 끼니별 (목표 시각, 끼니 시간대 시작, 끝). 그날 활동 시간과 60분 이상 겹치면 추천합니다.
MEALS = {
    "BREAKFAST": (time(8, 0), time(7, 0), time(10, 0)),
    "LUNCH": (time(12, 0), time(11, 0), time(14, 0)),
    "DINNER": (time(18, 0), time(17, 0), time(20, 0)),
}
MEAL_OVERLAP_MINUTES = 60
MEAL_EARLY_TOLERANCE_MINUTES = 30     # 목표 시각보다 이만큼 이르면 기다렸다가 식사
# 1단계에서 숙소 위치를 모를 때 비워 둘 숙소 이동 기준 거리(km)
HOTEL_TRANSFER_KM = {"WALK": 1, "PUBLIC_TRANSPORT": 3, "CAR": 5}


@dataclass
class DayPlan:
    index: int
    date: date
    transport: str
    pace: str
    hard_start: datetime
    soft_start: datetime
    soft_end: datetime
    hard_end: datetime
    needs_hotel: bool
    meals: list[str]
    tour_min: int
    tour_max: int

    @property
    def day_number(self) -> int:
        return self.index + 1

    @property
    def soft_minutes(self) -> int:
        return minutes_between(self.soft_start, self.soft_end)

    def summary(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "weekday": WEEKDAYS[self.date.weekday()],
            "transport_type": self.transport,
            "pace_type": self.pace,
            "start": self.soft_start.strftime("%H:%M"),
            "end": self.soft_end.strftime("%H:%M"),
            "latest_end": self.hard_end.strftime("%H:%M"),
            "needs_accommodation": self.needs_hotel,
            "meals": self.meals,
            "tour_min": self.tour_min,
            "tour_max": self.tour_max,
        }


@dataclass
class Visit:
    place: Place
    meal_type: str | None = None

    @property
    def stay_category(self) -> str:
        # 끼니로 쓴 시장은 식당 기준 체류시간을 씁니다.
        return "식당" if self.meal_type else self.place.category

    @property
    def is_tour(self) -> bool:
        # 끼니로 쓴 시장은 관광지 수에 세지 않습니다.
        return self.place.category == "관광" and self.meal_type is None


@dataclass
class TimedVisit:
    place: Place
    meal_type: str | None
    start: datetime
    end: datetime


@dataclass
class DaySchedule:
    departure: datetime
    visits: list[TimedVisit] = field(default_factory=list)
    hotel_arrival: datetime | None = None
    spare_minutes: int = 0


class TimeShortage(InvalidModelOutput):
    def __init__(self, day: DayPlan, minutes: int):
        super().__init__(f"{day.date.isoformat()}: 일정이 {minutes}분 초과합니다. 장소를 줄이거나 가까운 곳을 고르세요")
        self.minutes = minutes


def minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def parse_hhmm(value: str | None, default: time) -> time:
    if not value:
        return default
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def ceil_minute(value: datetime) -> datetime:
    # 도착 시각을 내림하지 않습니다 (13:00:30 도착이면 13:01부터 방문).
    if value.second or value.microsecond:
        return value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return value


def reserve_minutes(transport: str) -> int:
    speed, overhead = TRAVEL_SPEEDS[transport]
    return max(5, math.ceil(HOTEL_TRANSFER_KM[transport] / speed * 60 + overhead))


def build_day_plans(request: TravelGenerationRequest, custom: CustomRequest) -> list[DayPlan]:
    arrival, departure = request.local_bounds()
    overrides = {o.day_number: o for o in custom.day_overrides}
    target_start = max(parse_hhmm(custom.start_time, TARGET_START), REST_END)
    target_return = parse_hhmm(custom.return_time, TARGET_RETURN)
    if target_return <= target_start:
        target_return = TARGET_RETURN
    plans = []
    count = (departure.date() - arrival.date()).days + 1
    for index in range(count):
        day = arrival.date() + timedelta(days=index)
        first, last = index == 0, index == count - 1
        override = overrides.get(index + 1)
        transport = (override.transport_type if override and override.transport_type else None) or request.preference.transport_type
        pace = (override.pace_type if override and override.pace_type else None) or request.preference.pace_type

        hard_start = datetime.combine(day, REST_END)
        if first:
            hard_start = max(hard_start, ceil_minute(arrival + timedelta(minutes=ARRIVAL_BUFFER_MINUTES)))
        hard_end = datetime.combine(day, LATEST_RETURN)
        if last:
            leave = (departure - timedelta(minutes=DEPARTURE_BUFFER_MINUTES)).replace(second=0, microsecond=0)
            hard_end = min(hard_end, leave)
        if hard_start.date() != day or hard_end < hard_start:
            hard_start = hard_end = max(min(hard_start, datetime.combine(day, LATEST_RETURN)), datetime.combine(day, REST_END))
        soft_start = min(max(hard_start, datetime.combine(day, target_start)), hard_end)
        soft_end = hard_end if last else max(soft_start, min(hard_end, datetime.combine(day, target_return)))

        minutes = minutes_between(soft_start, soft_end)
        meals = [
            meal for meal, (_, begin, end) in MEALS.items()
            if minutes_between(max(soft_start, datetime.combine(day, begin)),
                               min(soft_end, datetime.combine(day, end))) >= MEAL_OVERLAP_MINUTES
        ]
        low, high = TOUR_COUNTS[pace]
        ratio = min(1.0, minutes / FULL_DAY_MINUTES)
        tour_min = math.floor(low * ratio)
        tour_max = min(high, max(tour_min, round(high * ratio)))
        plans.append(DayPlan(
            index=index, date=day, transport=transport, pace=pace,
            hard_start=hard_start, soft_start=soft_start, soft_end=soft_end, hard_end=hard_end,
            needs_hotel=not last, meals=meals, tour_min=tour_min, tour_max=tour_max,
        ))
    return plans


def validate_generation_window(plans: list[DayPlan], required: list[Place]) -> None:
    capacity = sum(p.tour_max + len(p.meals) for p in plans)
    if capacity == 0:
        raise GenerationFailed("여행 시간이 너무 짧습니다.", reason="insufficient_trip_time")
    if len([p for p in required if p.category != "숙소"]) > capacity:
        raise GenerationFailed("필수 장소가 너무 많습니다.", reason="too_many_required_places")


def schedule_day(plan: DayPlan, visits: list[Visit], transfers: list[int], tail: int) -> DaySchedule:
    """그날 활동 시간 안에 방문지를 배치합니다.

    transfers[i]는 visits[i]까지 가는 이동시간(분)입니다. 0번은 전날 숙소에서 출발하는 시간이고, 첫날은 0입니다.
    tail은 마지막 방문지에서 그날 숙소까지 가는 이동시간입니다.
    먼저 권장 시간 안에 배치하고, 모자라면 저녁을 23:59까지, 그다음 아침을 07:00까지 늘립니다.
    그래도 모자라면 TimeShortage를 냅니다.
    """
    policy = STAY_MINUTES[plan.pace]
    minimums = [policy[v.stay_category][0] for v in visits]
    required = sum(transfers) + sum(minimums) + tail
    start, end = plan.soft_start, plan.soft_end
    shortage = required - minutes_between(start, end)
    if shortage > 0:
        end = min(plan.hard_end, end + timedelta(minutes=shortage))
        shortage = required - minutes_between(start, end)
    if shortage > 0:
        start = max(plan.hard_start, start - timedelta(minutes=shortage))
        shortage = required - minutes_between(start, end)
    if shortage > 0:
        raise TimeShortage(plan, shortage)

    cursor, remaining = start, required
    timed = []
    for visit, transfer, minimum in zip(visits, transfers, minimums):
        cursor += timedelta(minutes=transfer)
        remaining -= transfer
        slack = max(0, minutes_between(cursor, end) - remaining)
        opens = opening_time(visit.place, cursor)
        if visit.meal_type and opens is not None and cursor < opens:
            # 곧 문을 여는 식당은 오픈 시각까지 기다립니다 (여유 시간 안에서만).
            wait = min(slack, minutes_between(cursor, opens))
            cursor += timedelta(minutes=wait)
            slack -= wait
        if visit.meal_type:
            target = datetime.combine(plan.date, MEALS[visit.meal_type][0])
            wait = min(slack, minutes_between(cursor, target - timedelta(minutes=MEAL_EARLY_TOLERANCE_MINUTES)))
            cursor += timedelta(minutes=wait)
            slack -= wait
        low, high = policy[visit.stay_category]
        stay = minimum + min(slack, (low + high) // 2 - low)
        remaining -= minimum
        timed.append(TimedVisit(visit.place, visit.meal_type, cursor, cursor + timedelta(minutes=stay)))
        cursor += timedelta(minutes=stay)
    hotel_arrival = cursor + timedelta(minutes=tail) if plan.needs_hotel else None
    return DaySchedule(
        departure=start, visits=timed, hotel_arrival=hotel_arrival,
        spare_minutes=minutes_between(hotel_arrival or cursor, end),
    )


def estimated_transfers(plan: DayPlan, visits: list[Visit], previous_hotel, hotel) -> tuple[list[int], int]:
    """좌표 기반 이동시간 추정. 숙소 위치를 모르면 숙소 이동 기준 거리로 계산합니다."""
    transfers, previous = [], previous_hotel
    for visit in visits:
        if previous is None:
            transfers.append(reserve_minutes(plan.transport) if plan.index > 0 else 0)
        else:
            transfers.append(travel_minutes(previous, visit.place, plan.transport))
        previous = visit.place
    if not plan.needs_hotel:
        return transfers, 0
    if hotel is None:
        return transfers, reserve_minutes(plan.transport)
    return transfers, travel_minutes(previous, hotel, plan.transport) if previous is not None else 0


def walking_km(plan: DayPlan, visits: list[Visit], previous_hotel, hotel) -> float:
    points = [p for p in [previous_hotel, *(v.place for v in visits), hotel] if p is not None]
    total = sum(path_km(a, b) for a, b in zip(points, points[1:]))
    if previous_hotel is None and plan.index > 0 and visits:
        total += HOTEL_TRANSFER_KM["WALK"]
    if hotel is None and plan.needs_hotel and visits:
        total += HOTEL_TRANSFER_KM["WALK"]
    return total


def check_walking(plan: DayPlan, km: float) -> None:
    if plan.transport == "WALK" and km > WALK_LIMIT_KM:
        raise InvalidModelOutput(f"{plan.date.isoformat()}: 도보 이동이 {km:.1f}km로 하루 상한 {WALK_LIMIT_KM}km를 넘습니다")


def validate_selection(
    plans: list[DayPlan], selection: ModelSelection, candidates: list[Place], required: list[Place]
) -> list[list[Visit]]:
    """모델의 선택을 모든 규칙으로 검증하고 날짜별 방문 목록을 돌려줍니다."""
    if [d.date for d in selection.days] != [p.date.isoformat() for p in plans]:
        raise InvalidModelOutput("모든 날짜를 순서대로 한 번씩 포함하세요: " + ", ".join(p.date.isoformat() for p in plans))
    by_id = {p.provider_place_id: p for p in candidates}
    seen: set[str] = set()
    result = []
    for plan, day in zip(plans, selection.days):
        visits = []
        for item in day.items:
            place = by_id.get(item.provider_place_id)
            if place is None or place.category == "숙소":
                raise InvalidModelOutput("후보 목록의 관광·식당 ID만 사용하세요")
            if place.provider_place_id in seen:
                raise InvalidModelOutput(f"{place.place_name}: 같은 장소를 여행 중 두 번 넣지 마세요")
            seen.add(place.provider_place_id)
            if place.category == "식당" and item.meal_type is None:
                raise InvalidModelOutput(f"{day.date}: 식당에는 meal_type을 지정하세요")
            meal = item.meal_type
            if place.category == "관광" and meal is not None:
                if not is_market(place):
                    meal = None  # 시장이 아닌 관광지의 끼니 표시는 무시합니다.
                elif meal not in MARKET_MEALS:
                    raise InvalidModelOutput(f"{place.place_name}: 시장은 점심·저녁 자리에만 넣을 수 있습니다")
            visits.append(Visit(place, meal))
        tours = sum(v.is_tour for v in visits)
        if not plan.tour_min <= tours <= plan.tour_max:
            raise InvalidModelOutput(f"{day.date}: 관광지는 {plan.tour_min}~{plan.tour_max}곳이어야 합니다 (현재 {tours})")
        meals = [v.meal_type for v in visits if v.meal_type]
        if meals != plan.meals:
            raise InvalidModelOutput(f"{day.date}: 식사는 {plan.meals} 순서로 한 번씩 넣으세요 (현재 {meals})")
        if "BREAKFAST" in meals and visits[0].meal_type != "BREAKFAST":
            raise InvalidModelOutput(f"{day.date}: 아침 식사는 그날 첫 항목이어야 합니다")
        market_tours = sum(v.is_tour and is_market(v.place) for v in visits)
        if market_tours > MARKET_TOURS_PER_DAY:
            raise InvalidModelOutput(f"{day.date}: 시장은 하루에 관광지로 {MARKET_TOURS_PER_DAY}곳까지 넣을 수 있습니다 (현재 {market_tours})")
        result.append(visits)

    required_ids = [p.provider_place_id for p in required if p.category != "숙소"]
    visited = [v.place.provider_place_id for day in result for v in day if v.place.is_required]
    if visited != required_ids:
        raise InvalidModelOutput("필수 장소를 모두 required_order 순서대로 포함하세요")

    for plan, visits in zip(plans, result):
        previous_hotel = None
        transfers, tail = estimated_transfers(plan, visits, previous_hotel, None)
        schedule = schedule_day(plan, visits, transfers, tail)
        check_walking(plan, walking_km(plan, visits, previous_hotel, None))
        closed = closed_meals(schedule)
        if closed:
            v = closed[0]
            if v.place.is_closed_on(v.start):
                reason = f"{WEEKDAYS[v.start.weekday()]}요일 휴무라 이 날짜에 넣을 수 없습니다. 이날 영업하는 식당을 고르세요"
            else:
                reason = (f"{v.place.open_time}~{v.place.close_time} 영업이라 {v.start.strftime('%H:%M')} 식사에 맞지 않습니다. "
                          "그 시각에 영업하는 식당을 고르세요")
            raise InvalidModelOutput(f"{plan.date.isoformat()}: {v.place.place_name}은 {reason}")
    return result


def opening_time(place: Place, at: datetime) -> datetime | None:
    """at 이후 MAX_OPENING_WAIT_MINUTES 안에 문을 여는 시각. 영업시간을 모르거나 휴무 요일이면 None입니다."""
    if not place.open_time or place.is_closed_on(at):
        return None
    opens = datetime.combine(at.date(), time.fromisoformat(place.open_time))
    return opens if at < opens <= at + timedelta(minutes=MAX_OPENING_WAIT_MINUTES) else None


def meal_start(place: Place, at: datetime, stay_minutes: int) -> datetime | None:
    """at 무렵에 식사할 수 있는 시작 시각. 곧 문을 열면 오픈 시각, 식사할 수 없으면 None입니다."""
    start = opening_time(place, at) or at
    return start if place.is_open_between(start, start + timedelta(minutes=stay_minutes)) is not False else None


def closed_meals(schedule: DaySchedule) -> list[TimedVisit]:
    """휴무 요일이거나 영업시간이 확인된 식당 중 배정된 식사 시각에 문을 닫는 곳. 정보가 없으면 문제로 보지 않습니다."""
    return [v for v in schedule.visits if v.meal_type and v.place.is_open_between(v.start, v.end) is False]
