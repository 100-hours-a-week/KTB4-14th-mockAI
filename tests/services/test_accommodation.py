import asyncio

import pytest

from app.core.exceptions import GenerationFailed
from app.schemas.planner import CustomRequest, DayOverride
from app.services.accommodation import assign_accommodations
from app.services.scheduler import Visit, build_day_plans
from tests.conftest import place, request_model


class HotelSearch:
    """검색 중심 좌표에 숙소를 돌려줍니다. min_radius보다 좁은 반경에서는 빈 결과를 줍니다."""

    def __init__(self, min_radius: int = 0, offset: float = 0.0):
        self.calls = []
        self.min_radius, self.offset = min_radius, offset

    async def accommodations(self, region_name, center, radius):
        self.calls.append(radius)
        if radius < self.min_radius:
            return []
        lat, lng = center.latitude + self.offset, center.longitude
        return [place(f"h-{lat:.3f}-{lng:.3f}", "숙소", lat, lng)]


def plans(transport="PUBLIC_TRANSPORT", overrides=()):
    custom = CustomRequest.empty().model_copy(update={"day_overrides": [
        DayOverride(day_number=day, transport_type=mode, pace_type=None) for day, mode in overrides
    ]})
    return build_day_plans(request_model(preference={"transport_type": transport}), custom)


def visits_at(*latitudes):
    return [[Visit(place(f"t{i}", "관광", lat))] for i, lat in enumerate(latitudes)]


def assign(plans_, visits, search):
    return asyncio.run(assign_accommodations(search, "부산광역시", plans_, visits, [], None))


def test_car_on_both_days_picks_a_new_hotel_each_night_near_the_midpoint():
    search = HotelSearch()

    hotels = assign(plans("CAR"), visits_at(35.10, 35.30, 35.50), search)

    assert hotels[0].latitude == pytest.approx(35.20)
    assert hotels[1].latitude == pytest.approx(35.40)
    assert hotels[2] is None
    assert search.calls == [10000, 10000]


def test_transit_keeps_last_nights_hotel_within_one_hour_each_way():
    search = HotelSearch()

    hotels = assign(plans(), visits_at(35.160, 35.165, 35.170), search)

    assert hotels[0] == hotels[1]
    assert len(search.calls) == 1


def test_transit_changes_hotel_when_a_leg_exceeds_one_hour():
    search = HotelSearch()

    hotels = assign(plans(), visits_at(35.10, 35.11, 35.60), search)

    assert hotels[0] != hotels[1]
    assert len(search.calls) == 2


def test_car_then_transit_night_follows_the_reuse_rule():
    search = HotelSearch()

    hotels = assign(plans("CAR", overrides=[(3, "PUBLIC_TRANSPORT")]), visits_at(35.160, 35.165, 35.170), search)

    assert hotels[0] == hotels[1]


def test_search_widens_once_when_nothing_is_found():
    search = HotelSearch(min_radius=6000)

    hotels = assign(plans(), visits_at(35.160, 35.165, 35.170), search)

    assert hotels[0] is not None
    assert search.calls[:2] == [5000, 7500]


def test_unfit_hotels_fail_after_the_widened_retry(caplog):
    search = HotelSearch(offset=2.0)

    with caplog.at_level("INFO", logger="uvicorn.error.audigo"), pytest.raises(GenerationFailed, match="숙소"):
        assign(plans(), visits_at(35.160, 35.165, 35.170), search)

    assert search.calls == [5000, 7500]
    assert "accommodation_unavailable" in caplog.text
    assert "shortage_minutes" in caplog.text
