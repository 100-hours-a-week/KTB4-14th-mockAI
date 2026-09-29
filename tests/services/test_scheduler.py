from datetime import datetime, time

import pytest

from app.core.exceptions import InvalidModelOutput
from app.schemas.planner import CustomRequest, DayOverride, ModelSelection
from app.services.scheduler import (
    TimeShortage, Visit, build_day_plans, check_walking, schedule_day, validate_selection,
)
from tests.conftest import place, request_model


def plans_for(**overrides):
    return build_day_plans(request_model(**overrides), CustomRequest.empty())


def test_first_day_starts_an_hour_after_arrival_and_last_day_ends_an_hour_before_departure():
    first, middle, last = plans_for()

    assert first.soft_start.time() == time(11, 0)
    assert middle.soft_start.time() == time(9, 0)
    assert middle.soft_end.time() == time(22, 30)
    assert last.hard_end.time() == time(17, 0)
    assert [p.needs_hotel for p in (first, middle, last)] == [True, True, False]


def test_nothing_is_scheduled_before_seven():
    first, *_ = plans_for(arrival_datetime="2026-09-19T05:00:00")

    assert first.hard_start.time() == time(7, 0)


@pytest.mark.parametrize("pace, counts", [("RELAXED", (2, 4)), ("BALANCED", (3, 5)), ("PACKED", (4, 6))])
def test_full_day_tour_counts_follow_pace(pace, counts):
    middle = plans_for(preference={"pace_type": pace})[1]

    assert (middle.tour_min, middle.tour_max) == counts


def test_short_days_scale_tour_counts_and_only_include_overlapping_meals():
    first, middle, last = plans_for()

    assert (first.tour_min, first.tour_max) == (2, 4)
    assert (last.tour_min, last.tour_max) == (1, 3)
    assert first.meals == ["LUNCH", "DINNER"]
    assert middle.meals == ["BREAKFAST", "LUNCH", "DINNER"]
    assert last.meals == ["BREAKFAST", "LUNCH"]


def test_custom_request_overrides_common_preference():
    custom = CustomRequest.empty().model_copy(update={
        "day_overrides": [DayOverride(day_number=2, transport_type="CAR", pace_type="PACKED")],
        "start_time": "10:00",
    })

    plans = build_day_plans(request_model(), custom)

    assert [p.transport for p in plans] == ["PUBLIC_TRANSPORT", "CAR", "PUBLIC_TRANSPORT"]
    assert plans[1].pace == "PACKED"
    assert plans[1].soft_start.time() == time(10, 0)


def full_day_visits():
    tours = [Visit(place(f"t{i}", "관광")) for i in range(5)]
    meals = [Visit(place(f"r{i}", "식당"), meal) for i, meal in enumerate(["BREAKFAST", "LUNCH", "DINNER"])]
    return [meals[0], tours[0], tours[1], meals[1], tours[2], tours[3], meals[2], tours[4]]


def test_overflow_extends_evening_to_2359_then_morning_back_toward_seven():
    middle = plans_for()[1]
    visits = full_day_visits()

    schedule = schedule_day(middle, visits, [60] * len(visits), 60)

    assert schedule.hotel_arrival == datetime(2026, 9, 20, 23, 59)
    assert schedule.departure == datetime(2026, 9, 20, 7, 44)


def test_day_that_cannot_fit_reports_the_shortage():
    middle = plans_for()[1]
    visits = full_day_visits()

    with pytest.raises(TimeShortage) as raised:
        schedule_day(middle, visits, [120] * len(visits), 60)

    assert raised.value.minutes > 0


def test_meal_waits_until_near_its_target_time_when_the_day_has_room():
    middle = plans_for()[1]
    visits = [Visit(place("r1", "식당"), "BREAKFAST"), Visit(place("r2", "식당"), "LUNCH")]

    schedule = schedule_day(middle, visits, [0, 0], 0)

    assert [v.start.time() for v in schedule.visits] == [time(9, 0), time(11, 30)]


def day_trip_plans():
    return plans_for(arrival_datetime="2026-09-19T08:00:00", departure_datetime="2026-09-19T20:00:00")


def candidates():
    return [place(f"t{i}", "관광", 35.16 + i * 0.001) for i in range(4)] + [
        place(f"r{i}", "식당", 35.16 + i * 0.001, 129.061) for i in range(4)
    ]


def selection(*items):
    return ModelSelection(title="부산", days=[{"date": "2026-09-19", "items": [
        {"provider_place_id": pid, "meal_type": meal} for pid, meal in items
    ]}])


VALID = [("r0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("t1", None), ("r2", "DINNER")]


def test_valid_selection_returns_visits():
    visits = validate_selection(day_trip_plans(), selection(*VALID), candidates(), [])

    assert [v.meal_type for v in visits[0]] == ["BREAKFAST", None, "LUNCH", None, "DINNER"]


@pytest.mark.parametrize("items, message", [
    ([("t0", None), ("r0", "BREAKFAST"), ("r1", "LUNCH"), ("t1", None), ("r2", "DINNER")], "아침"),
    ([("r0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("t1", None)], "식사"),
    ([("r0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("r2", "DINNER")], "관광지"),
    ([("r0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("t0", None), ("r2", "DINNER")], "두 번"),
    ([("r0", "BREAKFAST"), ("t0", None), ("r1", None), ("t1", None), ("r2", "DINNER")], "meal_type"),
])
def test_invalid_selection_explains_what_to_fix(items, message):
    with pytest.raises(InvalidModelOutput, match=message):
        validate_selection(day_trip_plans(), selection(*items), candidates(), [])


def test_required_places_must_keep_their_order():
    pool = candidates()
    pool[0] = pool[0].model_copy(update={"is_required": True, "required_order": 2})
    pool[1] = pool[1].model_copy(update={"is_required": True, "required_order": 1})

    with pytest.raises(InvalidModelOutput, match="필수 장소"):
        validate_selection(day_trip_plans(), selection(*VALID), pool, [pool[1], pool[0]])


def test_walking_day_is_capped_at_50km():
    plan = plans_for(preference={"transport_type": "WALK"})[1]

    check_walking(plan, 49.9)
    with pytest.raises(InvalidModelOutput, match="50km"):
        check_walking(plan, 50.1)


def market_candidates():
    return candidates() + [place("m0", "관광", 35.1605, 129.0605, source_category="쇼핑 > 전통시장")]


def test_market_can_fill_lunch_and_does_not_count_as_a_tour():
    items = [("r0", "BREAKFAST"), ("t0", None), ("m0", "LUNCH"), ("t1", None), ("r2", "DINNER")]

    visits = validate_selection(day_trip_plans(), selection(*items), market_candidates(), [])

    market = visits[0][2]
    assert market.meal_type == "LUNCH" and not market.is_tour and market.stay_category == "식당"


def test_market_cannot_be_breakfast():
    items = [("m0", "BREAKFAST"), ("t0", None), ("r1", "LUNCH"), ("t1", None), ("r2", "DINNER")]

    with pytest.raises(InvalidModelOutput, match="점심·저녁"):
        validate_selection(day_trip_plans(), selection(*items), market_candidates(), [])


def test_meal_label_on_a_non_market_tour_is_ignored():
    items = [("r0", "BREAKFAST"), ("t0", "LUNCH"), ("r1", "LUNCH"), ("t1", None), ("r2", "DINNER")]

    visits = validate_selection(day_trip_plans(), selection(*items), candidates(), [])

    assert visits[0][1].meal_type is None


def test_only_one_market_per_day_as_a_tour():
    pool = candidates() + [place(f"m{i}", "관광", 35.1605 + i * 0.0005, 129.0605, source_category="쇼핑 > 전통시장") for i in range(2)]
    items = [("r0", "BREAKFAST"), ("m0", None), ("r1", "LUNCH"), ("m1", None), ("r2", "DINNER")]

    with pytest.raises(InvalidModelOutput, match="시장은 하루에 관광지로 1곳"):
        validate_selection(day_trip_plans(), selection(*items), pool, [])


def test_restaurant_closed_at_meal_time_is_rejected():
    pool = [p.model_copy(update={"open_time": "11:00", "close_time": "21:00"}) if p.provider_place_id == "r0" else p
            for p in candidates()]

    with pytest.raises(InvalidModelOutput, match="11:00~21:00 영업"):
        validate_selection(day_trip_plans(), selection(*VALID), pool, [])


def test_unknown_hours_are_allowed():
    validate_selection(day_trip_plans(), selection(*VALID), candidates(), [])


def test_late_night_hours_wrap_past_midnight():
    bar = place("b", "식당", open_time="17:00", close_time="02:00")

    assert bar.is_open_between(datetime(2026, 9, 19, 23, 0), datetime(2026, 9, 20, 0, 30)) is True
    assert bar.is_open_between(datetime(2026, 9, 19, 12, 0), datetime(2026, 9, 19, 13, 0)) is False
    assert place("c", "식당").is_open_between(datetime(2026, 9, 19, 8, 0), datetime(2026, 9, 19, 9, 0)) is None


def test_restaurant_closed_on_that_weekday_is_rejected():
    # 2026-09-19는 토요일입니다.
    pool = [p.model_copy(update={"closed_days": ["토"]}) if p.provider_place_id == "r1" else p for p in candidates()]

    with pytest.raises(InvalidModelOutput, match="토요일 휴무"):
        validate_selection(day_trip_plans(), selection(*VALID), pool, [])


def test_day_plans_tell_the_model_the_weekday():
    assert plans_for()[0].summary()["weekday"] == "토"


def test_meal_waits_for_a_restaurant_opening_within_90_minutes():
    middle = plans_for()[1]
    brunch = place("r1", "식당", open_time="10:00", close_time="21:00")

    schedule = schedule_day(middle, [Visit(brunch, "BREAKFAST")], [0], 0)

    assert schedule.visits[0].start.time() == time(10, 0)


def test_restaurant_opening_too_late_is_still_rejected():
    pool = [p.model_copy(update={"open_time": "11:00", "close_time": "21:00"}) if p.provider_place_id == "r0" else p
            for p in candidates()]

    with pytest.raises(InvalidModelOutput, match="11:00~21:00"):
        validate_selection(day_trip_plans(), selection(*VALID), pool, [])
