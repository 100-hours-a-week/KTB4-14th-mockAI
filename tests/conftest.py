import copy
import json
import os

import pytest
from fastapi.testclient import TestClient

# app.main은 import 시점에 환경변수로 앱을 만듭니다. 로컬 .env 값이 테스트에 섞이지 않게 막습니다.
os.environ["MOCK_SCENARIO"] = "success"
os.environ.setdefault("AUDIGO_API_TOKEN", "t" * 32)

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.itinerary import Place, TravelGenerationRequest  # noqa: E402


TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MOCK_PATH = "/mock/api/ai/v1/itinerary-jobs/stream"
STREAM_PATH = "/api/ai/v1/itinerary-jobs/stream"
GENERATE_PATH = "/internal/ai/itineraries/generate"

BASE_BODY = {
    "travel_plan_id": 10,
    "region_id": 1,
    "region_name": "부산광역시",
    "arrival_datetime": "2026-09-19T10:00:00",
    "departure_datetime": "2026-09-21T18:00:00",
    "headcount": 2,
    "companion_type": "COUPLE",
    "preference": {
        "pace_type": "BALANCED", "transport_type": "PUBLIC_TRANSPORT", "budget_type": "KRW",
        "distance_preference": 50, "themes": ["NATURE"], "foods": ["KOREAN"],
    },
    "required_places": [],
}


def body(**overrides) -> dict:
    value = copy.deepcopy(BASE_BODY)
    preference = overrides.pop("preference", {})
    value.update(overrides)
    value["preference"].update(preference)
    return value


def request_model(**overrides) -> TravelGenerationRequest:
    return TravelGenerationRequest.model_validate(body(**overrides))


def place(pid: str, category: str, lat: float = 35.16, lng: float = 129.06, **extra) -> Place:
    return Place(provider_place_id=pid, place_name=f"장소 {pid}", address="부산광역시 테스트로",
                 latitude=lat, longitude=lng, category=category, **extra)


def events(text: str) -> list[tuple[str, dict]]:
    return [
        (block.split("event: ", 1)[1].splitlines()[0], json.loads(block.split("data: ", 1)[1]))
        for block in text.split("\n\n") if "data: " in block
    ]


@pytest.fixture
def settings() -> Settings:
    return Settings(api_token=TOKEN, openai_api_key="test", kakao_rest_api_key="test", mock_stage_delay_seconds=0)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client
