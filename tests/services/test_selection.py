from app.schemas.planner import ModelSelection
from app.services.scheduler import validate_selection
from app.services.selection import complete_selection
from tests.conftest import place
from tests.services.test_scheduler import candidates, day_trip_plans


def select(*items):
    return ModelSelection(title="부산", days=[{"date": "2026-09-19", "items": [
        {"provider_place_id": pid, "meal_type": meal} for pid, meal in items
    ]}])


def completed(*items):
    plans = day_trip_plans()
    result = complete_selection(plans, select(*items), candidates())
    validate_selection(plans, result, candidates(), [])
    return [(i.provider_place_id, i.meal_type) for i in result.days[0].items]


def test_breakfast_is_moved_to_the_front():
    items = completed(("t0", None), ("r0", "BREAKFAST"), ("r1", "LUNCH"), ("t1", None), ("r2", "DINNER"))

    assert items[0] == ("r0", "BREAKFAST")


def test_missing_meals_are_filled_with_nearby_restaurants():
    items = completed(("t0", None), ("r1", "LUNCH"), ("t1", None))

    assert [meal for _, meal in items if meal] == ["BREAKFAST", "LUNCH", "DINNER"]


def test_unlabeled_restaurants_fill_open_meals_and_extras_are_dropped():
    items = completed(("r0", None), ("t0", None), ("r1", None), ("t1", None), ("r2", None), ("r3", None))

    assert [pid for pid, meal in items if meal] == ["r0", "r1", "r2"]


def test_tour_count_is_brought_into_range():
    too_few = completed(("r0", "BREAKFAST"), ("r1", "LUNCH"), ("r2", "DINNER"))
    too_many = completed(*[(f"t{i}", None) for i in range(4)], ("r0", "BREAKFAST"))

    assert sum(pid.startswith("t") for pid, _ in too_few) == 2
    assert sum(pid.startswith("t") for pid, _ in too_many) == 4


def test_lunch_lands_near_noon_between_tours():
    items = completed(("r0", "BREAKFAST"), ("t0", None), ("t1", None), ("t2", None), ("r1", "LUNCH"), ("r2", "DINNER"))

    assert [pid for pid, _ in items].index("r1") < [pid for pid, _ in items].index("t2")


def test_hotels_and_unknown_ids_are_dropped():
    pool = candidates() + [place("h1", "숙소")]
    plans = day_trip_plans()

    result = complete_selection(plans, select(("h1", None), ("zzz", None), *[("t0", None), ("t1", None)]), pool)

    assert all(i.provider_place_id not in {"h1", "zzz"} for i in result.days[0].items)
