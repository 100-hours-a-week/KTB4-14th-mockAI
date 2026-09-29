from fastapi.testclient import TestClient

from app.clients.mock import MockPlaces, MockRoutes, MockSearch
from app.core.config import Settings
from app.main import create_app
from app.services.mock_generator import MockPlanner
from app.services.pipeline import Providers
from tests.conftest import AUTH, GENERATE_PATH, STREAM_PATH, TOKEN, body, events


def live_client(**settings_overrides):
    settings = Settings(**{"api_token": TOKEN, "openai_api_key": "test", "kakao_rest_api_key": "test", **settings_overrides})
    providers = Providers(places=MockPlaces(10), routes=MockRoutes(), search=MockSearch(), planner=MockPlanner())
    return TestClient(create_app(settings=settings, providers=providers))


def test_generate_returns_the_itinerary_json():
    with live_client() as client:
        response = client.post(GENERATE_PATH, json=body(), headers=AUTH)

    data = response.json()
    assert response.status_code == 200
    assert data["travel_plan_id"] == 10
    assert len(data["days"]) == 3
    assert data["days"][0]["items"][-1]["item_type"] == "ACCOMMODATION"


def test_stream_uses_the_same_pipeline():
    with live_client() as client:
        received = events(client.post(STREAM_PATH, json=body(), headers=AUTH).text)

    assert received[-1][0] == "complete"


def test_auth_is_required_on_every_generation_route():
    with live_client() as client:
        for path in (GENERATE_PATH, STREAM_PATH, "/mock" + STREAM_PATH):
            assert client.post(path, json=body()).status_code == 401
            assert client.post(path, json=body(), headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_missing_provider_keys_return_503():
    with live_client(openai_api_key=None) as client:
        response = client.post(GENERATE_PATH, json=body(), headers=AUTH)

    assert response.status_code == 503


def test_only_the_backend_request_format_is_accepted():
    nested = {"generation_job_id": 1, "region": {"region_id": 1, "full_name": "부산광역시"}}
    with live_client() as client:
        response = client.post(GENERATE_PATH, json=nested, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["message"] == "invalid_request"
    assert "필수 값입니다." in response.json()["data"]["error_message"]


def test_too_short_trip_is_422_with_the_travel_plan_id():
    short = body(arrival_datetime="2026-09-19T10:00:00", departure_datetime="2026-09-19T11:30:00")
    with live_client() as client:
        response = client.post(GENERATE_PATH, json=short, headers=AUTH)

    assert response.status_code == 422
    assert response.json() == {"message": "ai_itinerary_generation_failed",
                               "data": {"travel_plan_id": 10, "error_message": "여행 시간이 너무 짧습니다."}}


def test_openapi_lists_exactly_the_three_generation_routes():
    with live_client() as client:
        paths = client.get("/openapi.json").json()["paths"]

    assert {p for p, methods in paths.items() if "post" in methods} == {
        GENERATE_PATH, STREAM_PATH, "/mock" + STREAM_PATH,
    }
