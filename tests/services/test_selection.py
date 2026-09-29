from app.schemas.planner import ModelSelection
from app.services.scheduler import validate_selection
from app.services.selection import complete_selection
from tests.conftest import place
from tests.services.test_scheduler import VALID, candidates, day_trip_plans


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


def market_pool():
    return candidates() + [place("m0", "관광", 35.1605, 129.0605, source_category="쇼핑 > 전통시장")]


def test_market_labeled_for_lunch_keeps_the_lunch_slot():
    plans = day_trip_plans()
    items = [("r0", "BREAKFAST"), ("t0", None), ("m0", "LUNCH"), ("t1", None), ("r2", "DINNER")]

    result = complete_selection(plans, select(*items), market_pool())
    validate_selection(plans, result, market_pool(), [])

    assert ("m0", "LUNCH") in [(i.provider_place_id, i.meal_type) for i in result.days[0].items]


def test_market_without_an_open_meal_slot_stays_a_tour():
    plans = day_trip_plans()
    items = [("r0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("m0", "LUNCH"), ("r2", "DINNER")]

    result = complete_selection(plans, select(*items), market_pool())
    validate_selection(plans, result, market_pool(), [])

    assert ("m0", None) in [(i.provider_place_id, i.meal_type) for i in result.days[0].items]


def test_second_market_tour_is_replaced_by_a_non_market_tour():
    plans = day_trip_plans()
    pool = candidates() + [place(f"m{i}", "관광", 35.1605 + i * 0.0005, 129.0605, source_category="쇼핑 > 전통시장") for i in range(2)]
    items = [("r0", "BREAKFAST"), ("m0", None), ("r1", "LUNCH"), ("m1", None), ("r2", "DINNER")]

    result = complete_selection(plans, select(*items), pool)
    validate_selection(plans, result, pool, [])

    ids = [i.provider_place_id for i in result.days[0].items]
    assert "m0" in ids and "m1" not in ids


def test_breakfast_closed_in_the_morning_is_swapped_for_an_open_one():
    plans = day_trip_plans()
    hours = {"r0": ("11:00", "21:00"), "r3": ("07:00", "20:00")}
    pool = [p.model_copy(update={"open_time": hours[p.provider_place_id][0], "close_time": hours[p.provider_place_id][1]})
            if p.provider_place_id in hours else p for p in candidates()]

    result = complete_selection(plans, select(*VALID), pool)
    validate_selection(plans, result, pool, [])

    assert result.days[0].items[0].provider_place_id != "r0"


def test_lunch_place_closed_that_weekday_is_swapped():
    plans = day_trip_plans()
    pool = [p.model_copy(update={"closed_days": ["토"]}) if p.provider_place_id == "r1" else p for p in candidates()]

    result = complete_selection(plans, select(*VALID), pool)
    validate_selection(plans, result, pool, [])

    assert "r1" not in [i.provider_place_id for i in result.days[0].items]


def test_breakfast_place_opening_soon_is_kept():
    plans = day_trip_plans()
    pool = [p.model_copy(update={"open_time": "10:00", "close_time": "21:00"}) if p.provider_place_id == "r0" else p
            for p in candidates()]

    result = complete_selection(plans, select(*VALID), pool)

    assert result.days[0].items[0].provider_place_id == "r0"
