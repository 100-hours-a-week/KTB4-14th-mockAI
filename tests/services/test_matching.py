from itertools import permutations

import pytest
from pydantic import ValidationError

from app.schemas.matching import MatchingRequest
from app.services.matching import RANKING_VERSION, recommend


def candidate(user_id, nickname, theme, pace, *, gender="FEMALE", profile_image_url=None):
    return {
        "user_id": user_id,
        "nickname": nickname,
        "profile_image_url": profile_image_url,
        "gender": gender,
        "theme": theme,
        "pace": pace,
    }


def request(**overrides):
    payload = {
        "request": {
            "matching_request_id": 10,
            "user_id": 1,
            "preferred_companion_gender": "ANY",
            "theme": ["NATURE", "FOOD"],
            "pace": "RELAXED",
            "budget_min": None,
            "budget_max": None,
        },
        "candidates": [],
    }
    payload.update(overrides)
    return MatchingRequest.model_validate(payload)


def recommend_request(payload: MatchingRequest):
    return recommend(
        request_conditions=payload.request,
        candidates=payload.candidates,
        limit=payload.limit,
    )


def test_recommend_filters_self_gender_and_opposite_pace_but_keeps_zero_overlap():
    payload = request(request={
        "matching_request_id": 10,
        "user_id": 1,
        "preferred_companion_gender": "FEMALE",
        "theme": ["NATURE", "FOOD"],
        "pace": "RELAXED",
        "budget_min": None,
        "budget_max": None,
    }, candidates=[
        candidate(1, "self", ["NATURE"], "RELAXED"),
        candidate(2, "wrong gender", ["NATURE", "FOOD"], "RELAXED", gender="MALE"),
        candidate(3, "opposite", ["NATURE", "FOOD"], "PACKED"),
        candidate(4, "zero", ["SNS"], "BALANCED", profile_image_url="image"),
    ])

    result = recommend_request(payload)

    assert result.filtered_count == 1
    assert result.response.model_dump() == {
        "candidates": [{
            "user_id": 4,
            "nickname": "zero",
            "profile_image_url": "image",
            "theme": ["SNS"],
            "pace": "BALANCED",
            "match_rate": 10,
        }],
        "count": 1,
    }


@pytest.mark.parametrize("preferred_gender, candidate_gender, included", [
    ("ANY", "FEMALE", True),
    ("ANY", "MALE", True),
    ("FEMALE", "FEMALE", True),
    ("FEMALE", "MALE", False),
    ("MALE", "FEMALE", False),
    ("MALE", "MALE", True),
])
def test_gender_filter(preferred_gender, candidate_gender, included):
    payload = request(request={
        "matching_request_id": 10,
        "user_id": 1,
        "preferred_companion_gender": preferred_gender,
        "theme": ["NATURE"],
        "pace": "RELAXED",
        "budget_min": None,
        "budget_max": None,
    }, candidates=[candidate(2, "candidate", ["NATURE"], "RELAXED", gender=candidate_gender)])

    result = recommend_request(payload)
    assert result.filtered_count == int(included)
    assert result.response.count == int(included)


def test_ordering_is_score_then_original_nickname_then_user_id_and_input_order_independent():
    candidates = [
        candidate(7, "beta", ["NATURE", "FOOD"], "RELAXED"),
        candidate(6, " alpha", ["NATURE", "FOOD"], "RELAXED"),
        candidate(5, " alpha", ["NATURE", "FOOD"], "RELAXED"),
        candidate(4, "low", ["NATURE"], "RELAXED"),
    ]
    expected = [(5, " alpha", 100), (6, " alpha", 100), (7, "beta", 100)]

    for order in permutations(candidates):
        result = recommend_request(request(candidates=list(order)))
        assert [
            (item.user_id, item.nickname, item.match_rate) for item in result.response.candidates
        ] == expected
        assert result.filtered_count == 4


def test_response_cards_preserve_original_metadata_and_theme_order():
    payload = request(candidates=[candidate(
        2,
        "  Na Na  ",
        ["REST", "NATURE", "FOOD"],
        "RELAXED",
        profile_image_url="  https://example.test/profile  ",
    )])

    result = recommend_request(payload)
    card = result.response.candidates[0]
    assert card.nickname == "  Na Na  "
    assert card.profile_image_url == "  https://example.test/profile  "
    assert card.theme == ["REST", "NATURE", "FOOD"]


def test_budget_is_validated_but_does_not_change_ranking():
    candidates = [candidate(2, "candidate", ["NATURE", "FOOD"], "RELAXED")]
    no_budget = recommend_request(request(candidates=candidates))
    budget = recommend_request(request(
        request={
            "matching_request_id": 10,
            "user_id": 1,
            "preferred_companion_gender": "ANY",
            "theme": ["NATURE", "FOOD"],
            "pace": "RELAXED",
            "budget_min": 0,
            "budget_max": 3_000_000,
        },
        candidates=candidates,
    ))
    assert no_budget.response == budget.response


def test_limit_default_and_override_are_applied_after_ranking():
    candidates = [candidate(i, str(i), ["NATURE", "FOOD"], "RELAXED") for i in range(2, 6)]

    default = recommend_request(request(candidates=candidates))
    assert default.response.count == 3
    assert default.filtered_count == 4

    one = recommend_request(request(candidates=candidates, limit=1))
    assert [(item.user_id, item.match_rate) for item in one.response.candidates] == [(2, 100)]
    assert one.filtered_count == 4


@pytest.mark.parametrize("candidates", [[], [candidate(1, "self", ["NATURE"], "RELAXED")], [
    candidate(2, "opposite", ["NATURE"], "PACKED"),
]])
def test_empty_self_only_and_all_ineligible_inputs_return_empty(candidates):
    result = recommend_request(request(candidates=candidates))
    assert result.response.candidates == []
    assert result.response.count == 0
    assert result.filtered_count == 0
    assert RANKING_VERSION == "theme-pace-v1"


@pytest.mark.parametrize("field,value", [
    ("matching_request_id", "10"),
    ("matching_request_id", True),
    ("matching_request_id", 1.0),
    ("user_id", "1"),
    ("budget_min", "1"),
    ("budget_min", True),
    ("budget_max", 3_000_001),
])
def test_condition_ids_and_budget_require_strict_bounded_integers(field, value):
    conditions = {
        "matching_request_id": 10,
        "user_id": 1,
        "preferred_companion_gender": "ANY",
        "theme": ["NATURE"],
        "pace": "RELAXED",
        "budget_min": None,
        "budget_max": None,
    }
    conditions[field] = value
    if field == "budget_min" and value is not None:
        conditions["budget_max"] = 100
    with pytest.raises(ValidationError):
        request(request=conditions)


@pytest.mark.parametrize("limit", ["3", True, 1.0, 0, 4])
def test_limit_requires_strict_bounded_integer(limit):
    with pytest.raises(ValidationError):
        request(limit=limit)


@pytest.mark.parametrize("payload", [
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "ANY", "theme": ["NATURE", "NATURE"], "pace": "RELAXED"}},
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "ANY", "theme": [], "pace": "RELAXED"}},
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "OTHER", "theme": ["NATURE"], "pace": "RELAXED"}},
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "ANY", "theme": ["NATURE"], "pace": "SLOW"}},
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "ANY", "theme": ["NATURE"], "pace": "RELAXED", "budget_min": 1, "budget_max": None}},
    {"request": {"matching_request_id": 10, "user_id": 1, "preferred_companion_gender": "ANY", "theme": ["NATURE"], "pace": "RELAXED", "budget_min": 100, "budget_max": 99}},
    {"candidates": [candidate(2, "  ", ["NATURE"], "RELAXED")]},
    {"candidates": [candidate(2, "ok", ["NATURE", "NATURE"], "RELAXED")]},
    {"candidates": [candidate(2, "ok", ["NATURE"], "RELAXED", gender="OTHER")]},
])
def test_request_validation_rejects_invalid_fields(payload):
    with pytest.raises(ValidationError):
        request(**payload)


def test_schema_rejects_duplicate_candidate_ids_and_extra_fields():
    with pytest.raises(ValidationError, match="duplicate user_id"):
        request(candidates=[
            candidate(2, "first", ["NATURE"], "RELAXED"),
            candidate(2, "second", ["FOOD"], "RELAXED"),
        ])
    with pytest.raises(ValidationError):
        request(candidates=[{
            **candidate(2, "valid", ["NATURE"], "RELAXED"),
            "unexpected": True,
        }])
    with pytest.raises(ValidationError):
        request(unexpected=True)


@pytest.mark.parametrize("candidate_id", ["2", True, 2.0, 0, -1])
def test_candidate_ids_require_positive_strict_integers(candidate_id):
    with pytest.raises(ValidationError):
        request(candidates=[candidate(candidate_id, "valid", ["NATURE"], "RELAXED")])
