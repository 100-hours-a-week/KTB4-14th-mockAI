import copy
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.docs.matching_examples import REQUEST_EXAMPLE, RESPONSE_EXAMPLE
from app.main import create_app
from tests.conftest import AUTH, TOKEN


PATH = "/api/ai/v1/matches/candidates"


def matching_body(*, request_overrides=None, **overrides):
    payload = copy.deepcopy(REQUEST_EXAMPLE)
    payload["request"].update(request_overrides or {})
    payload.update(overrides)
    return payload


def test_documented_example_returns_exact_backend_card_contract(client):
    response = client.post(PATH, json=matching_body(), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == RESPONSE_EXAMPLE
    assert set(response.json()) == {"candidates", "count"}
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-request-id"].startswith("req_")


def test_default_limit_and_explicit_limit(client):
    payload = matching_body()
    assert "limit" not in payload
    assert client.post(PATH, json=payload, headers=AUTH).json()["count"] == 2
    payload["limit"] = 1
    assert client.post(PATH, json=payload, headers=AUTH).json()["candidates"] == [
        RESPONSE_EXAMPLE["candidates"][0],
    ]


@pytest.mark.parametrize("preference,expected_ids", [
    ("ANY", [23, 25, 24]), ("FEMALE", [23, 24]), ("MALE", [25]),
])
def test_gender_is_a_filter_not_a_score_component(client, preference, expected_ids):
    payload = matching_body(request_overrides={"preferred_companion_gender": preference})
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert [candidate["user_id"] for candidate in data["candidates"]] == expected_ids
    assert data["count"] == len(expected_ids)
    expected_scores = {23: 100, 24: 50, 25: 100}
    assert all(candidate["match_rate"] == expected_scores[candidate["user_id"]] for candidate in data["candidates"])


@pytest.mark.parametrize("budget_min,budget_max", [
    (None, None), (0, 0), (0, 3000000), (3000000, 3000000), (100000, 800000),
])
def test_valid_budget_never_changes_recommendations(client, budget_min, budget_max):
    payload = matching_body(request_overrides={"budget_min": budget_min, "budget_max": budget_max})
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == RESPONSE_EXAMPLE


def test_budget_fields_can_be_omitted_and_default_to_null(client):
    payload = matching_body()
    del payload["request"]["budget_min"]
    del payload["request"]["budget_max"]
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == RESPONSE_EXAMPLE


@pytest.mark.parametrize("budget_min,budget_max", [
    (None, 1), (1, None), (1, 0), (-1, 1), (0, 3000001), (3000001, 3000001),
    (True, 1), (0, True), ("0", 1), (0, "1"), (0.0, 1), (0, 1.0), ([], 1), (0, {}),
])
def test_invalid_budget_rejects_whole_request(client, budget_min, budget_max):
    payload = matching_body(request_overrides={"budget_min": budget_min, "budget_max": budget_max})
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert set(response.json()["data"]) == {"error_message"}
    assert "budget" in response.json()["data"]["error_message"]


@pytest.mark.parametrize("present,absent", [("budget_min", "budget_max"), ("budget_max", "budget_min")])
def test_one_omitted_budget_requires_the_other_to_be_null(client, present, absent):
    payload = matching_body()
    del payload["request"][absent]
    payload["request"][present] = None
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 200
    payload["request"][present] = 1
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422


@pytest.mark.parametrize("scope,field", [
    ("outer", "request"), ("outer", "candidates"),
    ("conditions", "matching_request_id"), ("conditions", "user_id"),
    ("conditions", "preferred_companion_gender"), ("conditions", "theme"), ("conditions", "pace"),
    ("candidate", "user_id"), ("candidate", "nickname"), ("candidate", "gender"),
    ("candidate", "theme"), ("candidate", "pace"),
])
def test_required_fields_are_not_invented(client, scope, field):
    payload = matching_body()
    target = {"outer": payload, "conditions": payload["request"], "candidate": payload["candidates"][0]}[scope]
    del target[field]
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert field in response.json()["data"]["error_message"]


def test_old_flat_contract_is_rejected(client):
    payload = matching_body()
    conditions = payload.pop("request")
    payload.update(conditions)
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422


@pytest.mark.parametrize("image", [None, "", "  https://EXAMPLE.invalid/A%2Fb?x=1&x=2  "])
def test_response_returns_only_candidate_ids_and_scores(client, image):
    payload = matching_body()
    candidate = payload["candidates"][0]
    candidate.update(nickname="  나나\t", profile_image_url=image, theme=["REST", "FOOD", "NATURE"])
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["candidates"][0] == {
        "user_id": candidate["user_id"], "match_rate": 100,
    }
    assert set(response.json()["candidates"][0]) == {"user_id", "match_rate"}


def test_missing_profile_image_is_returned_as_explicit_null(client):
    payload = matching_body()
    for candidate in payload["candidates"]:
        del candidate["profile_image_url"]
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == RESPONSE_EXAMPLE


def test_nickname_sort_uses_original_whitespace_and_limit_after_all_filters(client):
    template = matching_body()["candidates"][0]
    candidates = [{**template, "user_id": user_id, "nickname": nickname} for user_id, nickname in [
        (40, "alpha"), (30, " alpha"), (20, " alpha"), (10, "zeta"),
    ]]
    candidates.extend([{**template, "user_id": 1}, {**template, "user_id": 2, "gender": "MALE"},
                       {**template, "user_id": 3, "pace": "PACKED"}])
    payload = matching_body(candidates=candidates)
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert [candidate["user_id"] for candidate in data["candidates"]] == [20, 30, 40]
    assert data["count"] == 3


@pytest.mark.parametrize("candidates", [
    [],
    [{"user_id": 1, "nickname": "본인", "gender": "FEMALE", "theme": ["NATURE"], "pace": "RELAXED"}],
    [{"user_id": 2, "nickname": "제외", "gender": "FEMALE", "theme": ["NATURE"], "pace": "PACKED"}],
    [{"user_id": 2, "nickname": "성별 제외", "gender": "MALE", "theme": ["NATURE"], "pace": "RELAXED"}],
])
def test_no_eligible_candidates_is_success(client, candidates):
    response = client.post(PATH, json=matching_body(candidates=candidates), headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "count": 0}


@pytest.mark.parametrize("field,value", [
    ("matching_request_id", 0), ("matching_request_id", True), ("matching_request_id", "10"),
    ("user_id", -1), ("user_id", 1.0),
    ("theme", []), ("theme", ["NATURE"] * 2), ("theme", ["NATURE", "FOOD", "REST", "SNS"]),
    ("theme", ["PRIVATE_INVALID_THEME"]), ("pace", "여유롭게"),
    ("preferred_companion_gender", None), ("preferred_companion_gender", "PRIVATE_INVALID_GENDER"),
    ("preferred_companion_gender", " FEMALE "), ("unknown", "PRIVATE_INPUT"),
])
def test_invalid_request_conditions_return_sanitized_422(client, field, value):
    response = client.post(PATH, json=matching_body(request_overrides={field: value}), headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert set(response.json()["data"]) == {"error_message"}
    assert "PRIVATE_" not in response.text


@pytest.mark.parametrize("field,value", [
    ("limit", 0), ("limit", 4), ("limit", "3"), ("limit", True), ("limit", 2.0),
    ("limit", None), ("candidates", None), ("unknown", "PRIVATE_INPUT"),
    ("request", None), ("request", []), ("request", "PRIVATE_INPUT"),
])
def test_invalid_request_returns_sanitized_422(client, field, value):
    response = client.post(PATH, json=matching_body(**{field: value}), headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert set(response.json()["data"]) == {"error_message"}
    assert "PRIVATE_" not in response.text


@pytest.mark.parametrize("override", [
    {"user_id": 0}, {"user_id": True}, {"user_id": "23"},
    {"nickname": "  "}, {"nickname": 123},
    {"nickname": "\t\n\u2003"}, {"nickname": None},
    {"gender": None}, {"gender": "ANY"}, {"gender": " FEMALE "}, {"gender": "PRIVATE_INVALID_GENDER"},
    {"profile_image_url": 123}, {"profile_image_url": []}, {"profile_image_url": {}},
    {"theme": []}, {"theme": ["NATURE", "FOOD", "REST", "SNS", "CULTURE"]},
    {"theme": ["NATURE", "NATURE"]}, {"theme": ["INVALID"]},
    {"pace": "DENSE"}, {"unknown": "PRIVATE_INPUT"},
])
def test_invalid_candidate_rejects_whole_request(client, override):
    payload = matching_body()
    payload["candidates"][0].update(override)
    response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert "PRIVATE_INPUT" not in response.text


def test_duplicate_ids_and_invalid_excluded_candidates_are_not_ignored(client):
    payload = matching_body()
    payload["candidates"][1]["user_id"] = 23
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422
    payload = matching_body()
    payload["candidates"][0].update(user_id=1, nickname=" ")
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422
    payload = matching_body()
    payload["candidates"][2]["theme"] = ["PRIVATE_INVALID_THEME"]  # Gender-filtered candidates are validated too.
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422


@pytest.mark.parametrize("content", ['{"limit":', "[]", "null", "{}"])
def test_malformed_or_wrong_shape_body_uses_matching_422(client, content):
    response = client.post(PATH, content=content, headers={**AUTH, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic wrong"}])
def test_matching_authentication(client, headers):
    response = client.post(PATH, json=matching_body(), headers=headers)
    assert response.status_code == 401
    assert response.json() == {"message": "unauthorized", "data": None}
    assert response.headers["www-authenticate"] == "Bearer"


def test_missing_service_token_is_503():
    with TestClient(create_app(settings=Settings())) as client:
        response = client.post(PATH, json=matching_body(), headers=AUTH)
    assert response.status_code == 503
    assert response.json()["message"] == "ai_service_unavailable"


def test_matching_needs_no_provider_keys_or_external_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Matching must not call external APIs")

    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    with TestClient(create_app(settings=Settings(api_token=TOKEN))) as client:
        response = client.post(PATH, json=matching_body(), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_configured_cap_is_inclusive_and_never_truncates():
    with TestClient(create_app(settings=Settings(api_token=TOKEN, matching_max_candidates=3))) as client:
        payload = matching_body(limit=1)
        assert client.post(PATH, json=payload, headers=AUTH).status_code == 200
        extra = copy.deepcopy(payload["candidates"][0])
        extra["user_id"] = 1  # Self still counts toward the input bound.
        payload["candidates"].append(extra)
        response = client.post(PATH, json=payload, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["message"] == "invalid_request"
    assert "3" in response.json()["data"]["error_message"]


def test_matching_validation_does_not_change_itinerary_400(client):
    assert client.post(PATH, json={}, headers=AUTH).status_code == 422
    response = client.post("/internal/ai/itineraries/generate", json={}, headers=AUTH)
    assert response.status_code == 400


def test_repeated_request_is_deterministic(client):
    first = client.post(PATH, json=matching_body(), headers=AUTH).json()
    assert client.post(PATH, json=matching_body(), headers=AUTH).json() == first


def test_openapi_has_typed_contract_and_matching_error_schema():
    settings = Settings(api_token=TOKEN, docs_username="admin", docs_password="docs-password")
    with TestClient(create_app(settings=settings)) as client:
        schema = client.get("/openapi.json", auth=("admin", "docs-password")).json()
        swagger_request = schema["paths"][PATH]["post"]["requestBody"]["content"]["application/json"]["examples"]["backend"]["value"]
        example_response = client.post(PATH, json=swagger_request, headers=AUTH)
        assert example_response.status_code == 200
        assert example_response.json() == RESPONSE_EXAMPLE
    operation = schema["paths"][PATH]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/MatchingRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/MatchingResponse")
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    assert operation["security"] == [{"HTTPBearer": []}]
    request_schema = schema["components"]["schemas"]["MatchingRequest"]
    assert set(request_schema["properties"]) == {"request", "candidates", "limit"}
    assert set(request_schema["required"]) == {"request", "candidates"}
    assert request_schema["properties"]["limit"]["default"] == 3
    conditions_ref = request_schema["properties"]["request"]["$ref"].rsplit("/", 1)[-1]
    conditions = schema["components"]["schemas"][conditions_ref]
    assert set(conditions["properties"]) == {
        "matching_request_id", "user_id", "preferred_companion_gender", "theme", "pace", "budget_min", "budget_max",
    }
    assert set(conditions["required"]) == {"matching_request_id", "user_id", "preferred_companion_gender", "theme", "pace"}
    for field in ("budget_min", "budget_max"):
        # FastAPI omits null-valued defaults from its OpenAPI JSON. Nullable type
        # and exclusion from "required" still describe the optional contract.
        assert {item["type"] for item in conditions["properties"][field]["anyOf"]} == {"integer", "null"}
        integer_schema = next(item for item in conditions["properties"][field]["anyOf"] if item["type"] == "integer")
        assert integer_schema["minimum"] == 0
        assert integer_schema["maximum"] == 3000000
    assert set(schema["components"]["schemas"]["MatchingResponse"]["properties"]) == {"candidates", "count"}
    card = schema["components"]["schemas"]["MatchingCard"]
    assert set(card["properties"]) == {"user_id", "match_rate"}
    # OpenAPI encoding also removes nulls within example dictionaries. Check
    # the supplied example without weakening the actual HTTP null-key contract.
    documented_request = copy.deepcopy(swagger_request)
    documented_response = copy.deepcopy(operation["responses"]["200"]["content"]["application/json"]["example"])
    for candidate in documented_request["candidates"]:
        candidate.setdefault("profile_image_url", None)
    assert documented_request == REQUEST_EXAMPLE
    assert documented_response == RESPONSE_EXAMPLE


def test_logs_have_metadata_but_no_profiles(client, caplog):
    payload = matching_body(limit=1)
    for candidate in payload["candidates"]:
        candidate["nickname"] = "PRIVATE_NICKNAME"
        candidate["profile_image_url"] = "https://PRIVATE_IMAGE.invalid/private"
    with caplog.at_level(logging.INFO, logger="uvicorn.error.audigo"):
        response = client.post(PATH, json=payload, headers=AUTH)
        payload["request"]["theme"] = ["PRIVATE_INVALID_THEME"]
        assert client.post(PATH, json=payload, headers=AUTH).status_code == 422
    entry = next(json.loads(r.message) for r in caplog.records if '"event": "matching_recommendation"' in r.message)
    assert set(entry) == {
        "event", "request_id", "matching_request_id", "input_count", "filtered_count", "output_count", "elapsed_ms", "ranking_version",
    }
    assert entry["request_id"] == response.headers["x-request-id"]
    assert entry["matching_request_id"] == payload["request"]["matching_request_id"]
    assert entry["input_count"] == 3
    assert entry["filtered_count"] == 2
    assert entry["output_count"] == 1
    assert entry["ranking_version"] == "theme-pace-v1"
    assert entry["elapsed_ms"] >= 0
    assert any(json.loads(r.message).get("reason") == "matching_validation" for r in caplog.records)
    assert "PRIVATE_" not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_invalid_candidate_cap_settings_are_rejected(value):
    with pytest.raises(RuntimeError, match="MATCHING_MAX_CANDIDATES"):
        Settings(matching_max_candidates=value)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "bad"])
def test_invalid_candidate_cap_environment_is_rejected(monkeypatch, tmp_path, value):
    monkeypatch.setenv("MATCHING_MAX_CANDIDATES", value)
    with pytest.raises(RuntimeError, match="MATCHING_MAX_CANDIDATES"):
        Settings.from_env(tmp_path / "absent.env")


def test_candidate_cap_environment_overrides_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MATCHING_MAX_CANDIDATES=5\n")
    monkeypatch.setenv("MATCHING_MAX_CANDIDATES", "7")
    assert Settings.from_env(env_file).matching_max_candidates == 7


def test_candidate_cap_has_provisional_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MATCHING_MAX_CANDIDATES", raising=False)
    assert Settings.from_env(tmp_path / "absent.env").matching_max_candidates == 1000


def test_nickname_length_boundary(client):
    payload = matching_body()
    payload["candidates"][0]["nickname"] = "가" * 500
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 200
    payload["candidates"][0]["nickname"] += "가"
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422
    payload["candidates"][0]["nickname"] = " " + "가" * 500  # Raw length, not stripped length.
    assert client.post(PATH, json=payload, headers=AUTH).status_code == 422


def test_body_byte_limit_is_inclusive_and_counts_utf8_bytes():
    payload = matching_body()
    payload["candidates"][0]["nickname"] = "가" * 100
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert len(content) > len(content.decode("utf-8"))
    for limit, status in [(len(content), 200), (len(content) - 1, 422)]:
        with TestClient(create_app(settings=Settings(api_token=TOKEN, matching_max_body_bytes=limit))) as client:
            response = client.post(PATH, content=content, headers={**AUTH, "Content-Type": "application/json"})
        assert response.status_code == status
        if status == 422:
            assert response.json()["message"] == "invalid_request"
            assert "가" not in response.text


def test_chunked_body_limit_rejects_before_json_decoding():
    settings = Settings(api_token=TOKEN, matching_max_body_bytes=5)
    with TestClient(create_app(settings=settings)) as client:
        response = client.post(PATH, content=iter([b"{", b"xxxxxx"]),
                               headers={**AUTH, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert "5" in response.json()["data"]["error_message"]
    assert "xxxxxx" not in response.text


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_invalid_body_limit_settings_are_rejected(value):
    with pytest.raises(RuntimeError, match="MATCHING_MAX_BODY_BYTES"):
        Settings(matching_max_body_bytes=value)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "bad"])
def test_invalid_body_limit_environment_is_rejected(monkeypatch, tmp_path, value):
    monkeypatch.setenv("MATCHING_MAX_BODY_BYTES", value)
    with pytest.raises(RuntimeError, match="MATCHING_MAX_BODY_BYTES"):
        Settings.from_env(tmp_path / "absent.env")


def test_body_limit_environment_overrides_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MATCHING_MAX_BODY_BYTES=100\n")
    monkeypatch.setenv("MATCHING_MAX_BODY_BYTES", "200")
    assert Settings.from_env(env_file).matching_max_body_bytes == 200
