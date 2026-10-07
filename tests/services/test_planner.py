import asyncio
import json

from app.schemas.planner import CustomRequest, ModelSelection, PriceExtraction
from app.services.planner import OpenAIPlanner, interpret_custom_request
from tests.conftest import request_model


def objects(schema):
    yield schema
    for definition in schema.get("$defs", {}).values():
        yield definition


def test_llm_output_schemas_are_strict_compatible():
    # OpenAI strict 모드는 모든 속성이 필수이고 추가 키가 없어야 합니다.
    for model in (CustomRequest, ModelSelection, PriceExtraction):
        for obj in objects(model.model_json_schema()):
            assert obj.get("additionalProperties") is False, model
            assert set(obj["required"]) == set(obj["properties"]), model


class FakeOpenAI:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def complete_json(self, messages, schema, name, **kwargs):
        self.calls.append((messages, schema))
        return json.dumps(self.reply)


def test_selection_schema_limits_dates_and_ids_to_the_context():
    openai = FakeOpenAI({"title": "부산", "days": [{"date": "2026-09-19", "items": []}]})
    context = {"day_plans": [{"date": "2026-09-19"}], "candidates": [{"provider_place_id": "p1"}]}

    asyncio.run(OpenAIPlanner(openai).select_places(context, "관광지를 늘리세요"))

    messages, schema = openai.calls[0]
    assert schema["$defs"]["SelectionDay"]["properties"]["date"]["enum"] == ["2026-09-19"]
    assert schema["$defs"]["SelectionItem"]["properties"]["provider_place_id"]["enum"] == ["p1"]
    assert "관광지를 늘리세요" in messages[-1]["content"]


def test_custom_request_is_skipped_without_extra_request():
    openai = FakeOpenAI({})

    custom = asyncio.run(interpret_custom_request(OpenAIPlanner(openai), request_model(), ["2026-09-19"]))

    assert custom == CustomRequest.empty()
    assert openai.calls == []


def test_out_of_range_day_overrides_are_dropped():
    reply = CustomRequest.empty().model_dump() | {
        "day_overrides": [{"day_number": 1, "transport_type": "CAR", "pace_type": None},
                          {"day_number": 5, "transport_type": "WALK", "pace_type": None}],
    }
    request = request_model(preference={"extra_request": "첫날은 렌터카"})

    custom = asyncio.run(interpret_custom_request(OpenAIPlanner(FakeOpenAI(reply)), request, ["2026-09-19", "2026-09-20"]))

    assert [o.day_number for o in custom.day_overrides] == [1]


class FlakyPlanner:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    async def parse_custom_request(self, context):
        self.calls += 1
        if self.calls <= self.failures:
            from app.core.exceptions import InvalidModelOutput
            raise InvalidModelOutput("broken")
        return CustomRequest.empty().model_copy(update={"places": ["해운대"]})


def test_custom_request_is_retried_once_so_requested_places_are_not_lost():
    request = request_model(preference={"extra_request": "해운대 꼭 가고 싶어요"})

    planner = FlakyPlanner(failures=1)
    custom = asyncio.run(interpret_custom_request(planner, request, ["2026-09-19"]))

    assert planner.calls == 2
    assert custom.places == ["해운대"]
