import pytest

from tests.conftest import AUTH, MOCK_PATH, body


def error_message(client, payload=None, content=None):
    headers = {**AUTH, "Content-Type": "application/json"}
    response = client.post(MOCK_PATH, json=payload, headers=headers) if content is None else \
        client.post(MOCK_PATH, content=content, headers=headers)
    assert response.status_code == 400
    assert response.json()["message"] == "invalid_request"
    return response.json()["data"]["error_message"]


@pytest.mark.parametrize("payload, expected", [
    (body(departure_datetime="2026-08-06T10:00:00"), "departure_datetime: arrival_datetime보다 늦어야 합니다."),
    (body(departure_datetime="2026-09-28T10:00:00"), "departure_datetime: 여행 기간은 도착일 포함 최대 8일입니다."),
    (body(arrival_datetime="2026-09-19T10:00:00+09:00"), "departure_datetime: arrival_datetime과 시간대 표기가 같아야 합니다"),
    (body(arrival_datetime="2026-09-19"), "arrival_datetime: 날짜와 시각을 함께 적어야 합니다"),
    (body(preference={"pace_type": "FAST"}), "preference.pace_type: RELAXED, BALANCED, PACKED 중 하나여야 합니다."),
    (body(preference={"transport_type": "BIKE"}), "preference.transport_type: WALK, PUBLIC_TRANSPORT, CAR 중 하나여야 합니다."),
    (body(preference={"budget_min": 5, "budget_max": 1}), "preference.budget_max: budget_min 이상이어야 합니다."),
    (body(preference={"budget_type": "won"}), "preference.budget_type: 대문자 3글자 통화 코드여야 합니다"),
    (body(preference={"distance_preference": 101}), "preference.distance_preference: 100 이하여야 합니다."),
    (body(preference={"themes": ["A", "B", "C", "D"]}), "preference.themes: 최대 3개까지 넣을 수 있습니다."),
    (body(headcount="two"), "headcount: 정수여야 합니다."),
    (body(headcount=0), "headcount: 1 이상이어야 합니다."),
    (body(required_places=[{"provider": "KAKAO", "provider_place_id": "1"}] * 2), "required_places: provider_place_id가 중복됐습니다."),
    (body(required_places=[{"provider": "KAKAO", "provider_place_id": "1", "order": 1},
                           {"provider": "KAKAO", "provider_place_id": "2", "order": 1}]), "required_places: order가 중복됐습니다."),
    (body(required_places=[{"provider": "KAKAO", "provider_place_id": "1", "place_type": "HOTEL"}]),
     "required_places[0].place_type: TOURISM, RESTAURANT, ACCOMMODATION 중 하나여야 합니다."),
])
def test_validation_errors_name_the_field_and_the_reason(client, payload, expected):
    assert expected in error_message(client, payload)


def test_missing_and_unknown_fields_are_both_reported(client):
    payload = {k: v for k, v in body(extra=1).items() if k != "region_name"}

    assert error_message(client, payload) == "region_name: 필수 값입니다. / extra: 지원하지 않는 필드입니다."


def test_json_syntax_error_points_at_the_position(client):
    message = error_message(client, content='{"foods": ["KOREAN",]}')

    assert message.startswith("요청 본문 ")
    assert "마지막 항목 뒤 쉼표" in message


def test_input_values_are_not_echoed(client):
    message = error_message(client, body(preference={"pace_type": "SECRET-VALUE"}))

    assert "SECRET-VALUE" not in message
