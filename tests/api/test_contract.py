"""요청·응답 필드 고정 테스트. 필드를 바꾸려면 백엔드와 합의한 뒤 이 목록도 함께 고쳐야 합니다."""
from app.schemas.itinerary import Preference, RequiredPlace, TravelGenerationRequest
from tests.api.test_itinerary import live_client
from tests.conftest import AUTH, GENERATE_PATH, MOCK_PATH, body, events


REQUEST_FIELDS = {"travel_plan_id", "region_id", "region_name", "arrival_datetime", "departure_datetime",
                  "headcount", "companion_type", "preference", "required_places"}
PREFERENCE_FIELDS = {"pace_type", "transport_type", "budget_min", "budget_max", "budget_type",
                     "distance_preference", "themes", "foods", "extra_request"}
REQUIRED_PLACE_FIELDS = {"provider", "provider_place_id", "place_name", "address", "latitude", "longitude",
                         "place_type", "order"}
RESPONSE_FIELDS = {"travel_plan_id", "title", "days"}
DAY_FIELDS = {"day_number", "travel_date", "items"}
ITEM_FIELDS = {"provider", "provider_place_id", "place_name", "address", "road_address", "latitude", "longitude",
               "category", "sequence", "item_type", "meal_type", "start_time", "end_time", "price_info",
               "route_from_previous"}
SSE_RESULT_FIELDS = {"title", "days"}
SSE_DAY_FIELDS = {"day_number", "travel_date", "items", "routes"}
SSE_ITEM_FIELDS = {"provider", "provider_place_id", "place_name", "address", "latitude", "longitude", "sequence",
                   "start_time", "end_time", "meal_type", "price_info", "place_type"}


def test_request_fields_are_fixed():
    assert set(TravelGenerationRequest.model_fields) == REQUEST_FIELDS
    assert set(Preference.model_fields) == PREFERENCE_FIELDS
    assert set(RequiredPlace.model_fields) == REQUIRED_PLACE_FIELDS


def test_json_response_fields_are_fixed():
    with live_client() as client:
        data = client.post(GENERATE_PATH, json=body(), headers=AUTH).json()

    assert set(data) == RESPONSE_FIELDS
    for day in data["days"]:
        assert set(day) == DAY_FIELDS
        for item in day["items"]:
            assert set(item) == ITEM_FIELDS


def test_sse_result_fields_are_fixed(client):
    result = dict(events(client.post(MOCK_PATH, json=body(), headers=AUTH).text))["ROUTE_OPTIMIZE_DONE"]["result"]

    assert set(result) == SSE_RESULT_FIELDS
    for day in result["days"]:
        assert set(day) == SSE_DAY_FIELDS
        for item in day["items"]:
            # 날을 넘는 구간(전날 숙소 → 첫 장소)만 항목에 route_from_previous가 붙습니다.
            assert set(item) - {"route_from_previous"} == SSE_ITEM_FIELDS
