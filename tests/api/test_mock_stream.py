from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import AUTH, MOCK_PATH, TOKEN, body, events


STAGES = [
    "PLACE_RECOMMEND_STARTED", "PLACE_RECOMMEND_DONE", "STAY_RECOMMEND_STARTED", "STAY_RECOMMEND_DONE",
    "ROUTE_OPTIMIZE_STARTED", "ROUTE_OPTIMIZE_DONE", "complete",
]


def test_mock_stream_follows_backend_event_order_with_one_result(client):
    response = client.post(MOCK_PATH, json=body(), headers=AUTH)

    received = events(response.text)
    assert response.status_code == 200
    assert [name for name, _ in received] == STAGES
    assert sum("result" in data for _, data in received) == 1
    assert "music" not in response.text
    result = dict(received)["ROUTE_OPTIMIZE_DONE"]["result"]
    assert [day["day_number"] for day in result["days"]] == [1, 2, 3]


def test_mock_itinerary_follows_schedule_rules(client):
    result = dict(events(client.post(MOCK_PATH, json=body(), headers=AUTH).text))["ROUTE_OPTIMIZE_DONE"]["result"]

    first, middle, last = result["days"]
    assert [i["meal_type"] for i in middle["items"] if i["meal_type"]] == ["BREAKFAST", "LUNCH", "DINNER"]
    assert middle["items"][0]["meal_type"] == "BREAKFAST"
    assert first["items"][0]["start_time"] >= "11:00"
    assert last["items"][-1]["end_time"] <= "17:00"
    for day in (first, middle):
        hotel = day["items"][-1]
        assert hotel["place_type"] == "ACCOMMODATION"
        assert hotel["end_time"] == "23:59"
    assert all(i["place_type"] != "ACCOMMODATION" for i in last["items"])
    assert all(i["start_time"] >= "07:00" for day in result["days"] for i in day["items"])


def test_mock_result_is_deterministic(client):
    first = client.post(MOCK_PATH, json=body(), headers=AUTH).text
    second = client.post(MOCK_PATH, json=body(), headers=AUTH).text

    assert first == second


def test_required_place_is_included(client):
    required = {"provider": "KAKAO", "provider_place_id": "26338954", "place_name": "해운대해수욕장",
                "latitude": 35.1587, "longitude": 129.1604, "place_type": "TOURISM", "order": 1}

    text = client.post(MOCK_PATH, json=body(required_places=[required]), headers=AUTH).text

    assert "26338954" in text


def test_header_scenario_fails_the_requested_stage(client):
    response = client.post(MOCK_PATH, json=body(), headers={**AUTH, "X-Mock-Scenario": "fail_stay"})

    received = events(response.text)
    assert [name for name, _ in received] == STAGES[:3] + ["error"]
    error = received[-1][1]
    assert error["stage"] == "STAY_RECOMMEND"
    assert error["message"] == "ai_itinerary_generation_failed"
    assert error["travel_plan_id"] == 10


def test_environment_scenario_applies_unless_the_header_overrides_it():
    settings = Settings(api_token=TOKEN, mock_stage_delay_seconds=0, mock_scenario="fail_place")
    with TestClient(create_app(settings=settings)) as client:
        by_env = events(client.post(MOCK_PATH, json=body(), headers=AUTH).text)
        by_header = events(client.post(MOCK_PATH, json=body(), headers={**AUTH, "X-Mock-Scenario": "success"}).text)

    assert by_env[-1][0] == "error" and by_env[-1][1]["stage"] == "PLACE_RECOMMEND"
    assert by_header[-1][0] == "complete"


def test_drop_ends_the_stream_without_a_result(client):
    received = events(client.post(MOCK_PATH, json=body(), headers={**AUTH, "X-Mock-Scenario": "drop"}).text)

    assert [name for name, _ in received] == STAGES[:5]


def test_unavailable_scenario_returns_503(client):
    response = client.post(MOCK_PATH, json=body(), headers={**AUTH, "X-Mock-Scenario": "unavailable"})

    assert response.status_code == 503
    assert response.json()["message"] == "ai_service_unavailable"


def test_unknown_scenario_is_rejected(client):
    response = client.post(MOCK_PATH, json=body(), headers={**AUTH, "X-Mock-Scenario": "boom"})

    assert response.status_code == 400
    assert response.json()["message"] == "invalid_request"


def test_breakfast_is_a_place_open_in_the_morning(client):
    result = dict(events(client.post(MOCK_PATH, json=body(), headers=AUTH).text))["ROUTE_OPTIMIZE_DONE"]["result"]

    breakfast = result["days"][1]["items"][0]
    assert breakfast["meal_type"] == "BREAKFAST"
    assert any(word in breakfast["place_name"] for word in ("국밥", "백반", "브런치", "해장"))
    assert "market_name" not in breakfast
