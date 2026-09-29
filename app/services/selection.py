"""모델 선택 보완. 모델은 장소를 고르고, 끼니 구조와 관광지 수는 서버가 맞춥니다.

- 후보 밖·숙소·중복 장소는 뺍니다.
- 관광지가 tour_max보다 많으면 필수가 아닌 것부터 뒤에서 빼고, tour_min보다 적으면 가까운 후보를 더합니다.
- 끼니: 모델이 붙인 meal_type을 먼저 존중하고, 남는 식당으로 빈 끼니를 채우고, 그래도 비면
  앞 장소에서 가장 가까운 식당을 넣습니다. 끼니보다 많은 식당은 뺍니다 (필수 식당은 유지).
- 순서: 아침은 맨 앞, 점심·저녁은 예상 시각이 목표보다 30분 이른 시점에 도달하는 자리에 넣습니다.

필수 장소 순서나 시간 부족처럼 보완할 수 없는 문제는 validate_selection이 잡아 모델에 다시 요청합니다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.geo import distance_km, travel_minutes
from app.core.logging import record
from app.schemas.itinerary import Place
from app.schemas.planner import ModelSelection
from app.services.scheduler import MEAL_EARLY_TOLERANCE_MINUTES, MEALS, STAY_MINUTES, DayPlan, reserve_minutes


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
    fixes = {"dropped": 0, "added_tours": 0, "removed_tours": 0, "added_meals": 0, "removed_meals": 0}

    for day in selection.days:
        tours, restaurants = [], []
        for item in day.items:
            place = by_id.get(item.provider_place_id)
            if place is None or place.category == "숙소" or place.provider_place_id in used:
                fixes["dropped"] += 1
                continue
            used.add(place.provider_place_id)
            (tours if place.category == "관광" else restaurants).append((place, item.meal_type))
        picked.append((tours, restaurants))

    spare_tours = [c for c in candidates if c.category == "관광" and c.provider_place_id not in used]
    spare_restaurants = [c for c in candidates if c.category == "식당" and c.provider_place_id not in used]

    days = []
    for plan, (tours, restaurants) in zip(plans, picked):
        tours = [place for place, _ in tours]
        while len(tours) > plan.tour_max and any(not t.is_required for t in tours):
            drop = next(t for t in reversed(tours) if not t.is_required)
            tours.remove(drop)
            spare_tours.append(drop)
            fixes["removed_tours"] += 1
        while len(tours) < plan.tour_min and spare_tours:
            extra = _nearest(spare_tours, tours[-1] if tours else (restaurants[0][0] if restaurants else None))
            spare_tours.remove(extra)
            tours.append(extra)
            fixes["added_tours"] += 1

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
            free = next((meal for meal, value in slots.items() if value is None), None)
            if free is not None:
                slots[free] = place
            else:
                # 끼니보다 많은 식당은 뺍니다. 필수 식당이 빠지면 검증에서 모델에 다시 요청합니다.
                spare_restaurants.append(place)
                fixes["removed_meals"] += 1

        # 순서: 예상 시각을 따라가며 점심·저녁을 끼워 넣습니다.
        policy = STAY_MINUTES[plan.pace]
        sequence: list[tuple[str | None, Place | None]] = []
        cursor = plan.soft_start + timedelta(minutes=reserve_minutes(plan.transport) if plan.index else 0)
        previous: Place | None = None

        def advance(place: Place | None, category: str) -> None:
            nonlocal cursor, previous
            if place is not None and previous is not None:
                cursor += timedelta(minutes=travel_minutes(previous, place, plan.transport))
            low, high = policy[category]
            cursor += timedelta(minutes=(low + high) // 2)
            previous = place or previous

        if "BREAKFAST" in slots:
            sequence.append(("BREAKFAST", slots["BREAKFAST"]))
            advance(slots["BREAKFAST"], "식당")
        pending = [meal for meal in ("LUNCH", "DINNER") if meal in slots]
        for tour in tours:
            while pending and cursor >= _meal_ready(plan, pending[0]):
                sequence.append((pending[0], slots[pending[0]]))
                advance(slots[pending[0]], "식당")
                pending.pop(0)
            sequence.append((None, tour))
            advance(tour, "관광")
        for meal in pending:
            sequence.append((meal, slots[meal]))

        # 비어 있는 끼니는 앞(없으면 뒤) 장소에서 가장 가까운 식당으로 채웁니다.
        items = []
        for index, (meal, place) in enumerate(sequence):
            if place is None:
                neighbors = [p for _, p in sequence[:index][::-1] + sequence[index + 1:] if p is not None]
                place = _nearest(spare_restaurants, neighbors[0] if neighbors else None)
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
