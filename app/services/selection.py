"""모델 선택 보완. 모델은 장소를 고르고, 끼니 구조와 관광지 수는 서버가 맞춥니다.

- 후보 밖·숙소·중복 장소는 뺍니다.
- 관광지가 tour_max보다 많으면 필수가 아닌 것부터 뒤에서 빼고, tour_min보다 적으면 가까운 후보를 더합니다.
- 끼니: 모델이 붙인 meal_type을 먼저 존중하고, 남는 식당으로 빈 끼니를 채우고, 그래도 비면
  앞 장소에서 가장 가까운 식당을 넣습니다. 끼니보다 많은 식당은 뺍니다.
  모델이 점심·저녁으로 지정한 시장도 끼니로 인정하고, 끼니 자리가 없으면 관광지로 둡니다.
- 순서: 아침은 맨 앞, 점심·저녁은 예상 시각이 목표보다 30분 이른 시점에 도달하는 자리에 넣습니다.
- 관광지로 들어간 시장은 하루 1곳까지만 두고, 나머지는 시장이 아닌 관광지로 바꿉니다.
- 휴무 요일이거나 예상 식사 시각에 문을 닫는 식당(90분 안에 여는 곳은 허용)은 그 시각에 식사할 수 있는 가까운 식당으로 바꿉니다.

필수 장소 순서나 시간 부족처럼 보완할 수 없는 문제는 validate_selection이 잡아 모델에 다시 요청합니다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.geo import distance_km, travel_minutes
from app.core.logging import record
from app.schemas.itinerary import Place
from app.schemas.planner import ModelSelection
from app.services.places import MARKET_MEALS, is_market
from app.core.exceptions import InvalidModelOutput
from app.services.scheduler import (
    MARKET_TOURS_PER_DAY, MEAL_EARLY_TOLERANCE_MINUTES, MEALS, STAY_MINUTES, DayPlan, Visit, closed_meals,
    estimated_transfers, meal_start, reserve_minutes, schedule_day,
)


def _nearest(pool: list[Place], anchor: Place | None) -> Place | None:
    if not pool:
        return None
    return min(pool, key=lambda p: distance_km(p, anchor)) if anchor else pool[0]


def complete_selection(
    plans: list[DayPlan], selection: ModelSelection, candidates: list[Place],
) -> ModelSelection:
    if [d.date for d in selection.days] != [p.date.isoformat() for p in plans]:
        return selection  # 날짜 오류는 보완하지 않고 검증에서 재요청합니다.
    by_id = {c.provider_place_id: c for c in candidates}
    used: set[str] = set()
    picked = []
    fixes = dict.fromkeys(("dropped", "added_tours", "removed_tours", "removed_markets", "added_meals",
                           "removed_meals", "swapped_closed"), 0)

    for day in selection.days:
        tours, restaurants = [], []
        for item in day.items:
            place = by_id.get(item.provider_place_id)
            if place is None or place.category == "숙소" or place.provider_place_id in used:
                fixes["dropped"] += 1
                continue
            used.add(place.provider_place_id)
            eats_here = place.category == "식당" or (is_market(place) and item.meal_type in MARKET_MEALS)
            (restaurants if eats_here else tours).append((place, item.meal_type))
        picked.append((tours, restaurants))

    spare_tours = [c for c in candidates if c.category == "관광" and c.provider_place_id not in used]
    spare_restaurants = [c for c in candidates if c.category == "식당" and c.provider_place_id not in used]

    days = []
    for plan, (tours, restaurants) in zip(plans, picked):
        tours = [place for place, _ in tours]
        # 끼니 배정: 필수 식당 → 표시가 맞는 식당 → 남는 식당 순서
        slots: dict[str, Place | None] = dict.fromkeys(plan.meals)
        ordered = sorted(restaurants, key=lambda r: not r[0].is_required)
        leftovers = []
        for place, label in ordered:
            if label in slots and slots[label] is None:
                slots[label] = place
            else:
                leftovers.append(place)
        for place in leftovers:
            allowed = MARKET_MEALS if place.category == "관광" else tuple(slots)
            free = next((meal for meal, value in slots.items() if value is None and meal in allowed), None)
            if free is not None:
                slots[free] = place
            elif place.category == "관광":
                tours.append(place)  # 끼니 자리가 없는 시장은 관광지로 둡니다.
            else:
                # 끼니보다 많은 식당은 뺍니다. 필수 식당이 빠지면 검증에서 모델에 다시 요청합니다.
                spare_restaurants.append(place)
                fixes["removed_meals"] += 1

        # 관광지로 들어간 시장은 하루 MARKET_TOURS_PER_DAY곳까지만 둡니다.
        markets = [t for t in tours if is_market(t)]
        # 모델이 먼저 고른 시장을 남기고 뒤에서부터 뺍니다 (필수 시장은 빼지 않음).
        for extra_market in [m for m in reversed(markets) if not m.is_required][: max(0, len(markets) - MARKET_TOURS_PER_DAY)]:
            tours.remove(extra_market)
            spare_tours.append(extra_market)
            fixes["removed_markets"] += 1
        while len(tours) > plan.tour_max and any(not t.is_required for t in tours):
            drop = next(t for t in reversed(tours) if not t.is_required)
            tours.remove(drop)
            spare_tours.append(drop)
            fixes["removed_tours"] += 1
        while len(tours) < plan.tour_min:
            market_full = sum(is_market(t) for t in tours) >= MARKET_TOURS_PER_DAY
            pool = [t for t in spare_tours if not (market_full and is_market(t))]
            extra = _nearest(pool, tours[-1] if tours else (restaurants[0][0] if restaurants else None))
            if extra is None:
                break
            spare_tours.remove(extra)
            tours.append(extra)
            fixes["added_tours"] += 1

        # 순서: 예상 시각을 따라가며 점심·저녁을 끼워 넣습니다. 끼니마다 예상 식사 시각을 함께 기록합니다.
        policy = STAY_MINUTES[plan.pace]
        sequence: list[tuple[str | None, Place | None, datetime]] = []
        cursor = plan.soft_start + timedelta(minutes=reserve_minutes(plan.transport) if plan.index else 0)
        previous: Place | None = None

        def advance(place: Place | None, category: str) -> None:
            nonlocal cursor, previous
            if place is not None and previous is not None:
                cursor += timedelta(minutes=travel_minutes(previous, place, plan.transport))
            low, high = policy[category]
            cursor += timedelta(minutes=(low + high) // 2)
            previous = place or previous

        def add_meal(meal: str) -> None:
            nonlocal cursor
            cursor = max(cursor, _meal_ready(plan, meal)) if meal != "BREAKFAST" else cursor
            sequence.append((meal, slots[meal], cursor))
            advance(slots[meal], "식당")

        if "BREAKFAST" in slots:
            add_meal("BREAKFAST")
        pending = [meal for meal in ("LUNCH", "DINNER") if meal in slots]
        for tour in tours:
            while pending and cursor >= _meal_ready(plan, pending[0]):
                add_meal(pending.pop(0))
            sequence.append((None, tour, cursor))
            advance(tour, "관광")
        for meal in pending:
            add_meal(meal)

        # 비어 있거나 그 시각에 문을 닫는 끼니는 앞(없으면 뒤) 장소에서 가장 가까운, 그 시각에 영업하는 식당으로 채웁니다.
        low, high = policy["식당"]
        stay = (low + high) // 2
        items = []
        for index, (meal, place, at) in enumerate(sequence):
            if meal is not None:
                if place is not None and meal_start(place, at, stay) is None and not place.is_required:
                    spare_restaurants.append(place)
                    place = None
                    fixes["swapped_closed"] += 1
                if place is None:
                    neighbors = [p for _, p, _ in sequence[:index][::-1] + sequence[index + 1:] if p is not None]
                    open_pool = [r for r in spare_restaurants if meal_start(r, at, stay) is not None]
                    place = _nearest(open_pool, neighbors[0] if neighbors else None)
                    if place is None:
                        continue
                    spare_restaurants.remove(place)
                    fixes["added_meals"] += 1
            items.append({"provider_place_id": place.provider_place_id, "meal_type": meal})
        days.append({"date": plan.date.isoformat(), "items": items})

    if any(fixes.values()):
        record("selection_completed", **fixes)
    return ModelSelection(title=selection.title, days=days)


def _meal_ready(plan: DayPlan, meal: str) -> datetime:
    target = datetime.combine(plan.date, MEALS[meal][0])
    return target - timedelta(minutes=MEAL_EARLY_TOLERANCE_MINUTES)


REPAIR_PASSES = 3  # 날마다 문 닫은 식당 교체를 반복하는 최대 횟수 (교체하면 동선이 바뀌어 다시 계산합니다)


def repair_closed_meals(plans: list[DayPlan], selection: ModelSelection, candidates: list[Place]) -> ModelSelection:
    """실제로 계산한 식사 시각에 휴무이거나 문을 닫는 식당을, 그 시각에 영업하는 가까운 식당으로 바꿉니다.

    complete_selection의 시각 추정과 schedule_day의 실제 배치가 조금 달라서 생기는 불일치를
    모델에 다시 묻지 않고 서버에서 고칩니다. 바꿀 식당이 없으면 그대로 두고 검증에서 재요청합니다.
    """
    by_id = {c.provider_place_id: c for c in candidates}
    used = {i.provider_place_id for d in selection.days for i in d.items}
    days = [d.model_copy(deep=True) for d in selection.days]
    swapped = 0
    for plan, day in zip(plans, days):
        for _ in range(REPAIR_PASSES):
            if any(i.provider_place_id not in by_id for i in day.items):
                break
            visits = [Visit(by_id[i.provider_place_id], i.meal_type if by_id[i.provider_place_id].category == "식당"
                            or (is_market(by_id[i.provider_place_id]) and i.meal_type in MARKET_MEALS) else None)
                      for i in day.items]
            try:
                transfers, tail = estimated_transfers(plan, visits, None, None)
                closed = closed_meals(schedule_day(plan, visits, transfers, tail))
            except InvalidModelOutput:
                break  # 시간 부족은 검증 단계에서 모델에 다시 요청합니다.
            changed = False
            stay = sum(STAY_MINUTES[plan.pace]["식당"]) // 2
            for visit in closed:
                if visit.place.is_required:
                    continue
                pool = [c for c in candidates if c.category == "식당" and c.provider_place_id not in used
                        and meal_start(c, visit.start, stay) is not None]
                replacement = _nearest(pool, visit.place)
                if replacement is None:
                    continue
                for item in day.items:
                    if item.provider_place_id == visit.place.provider_place_id:
                        item.provider_place_id = replacement.provider_place_id
                used.add(replacement.provider_place_id)
                swapped += 1
                changed = True
            if not changed:
                break
    if swapped:
        record("closed_meals_repaired", swapped=swapped)
    return ModelSelection(title=selection.title, days=days)
