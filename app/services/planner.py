"""LLM 단계: 커스텀 요청 해석, 장소 선택, 가격 추출."""
from __future__ import annotations

import json

from pydantic import ValidationError

from app.clients.openai_client import OpenAIClient
from app.core.exceptions import GenerationFailed, InvalidModelOutput
from app.core.geo import travel_minutes
from app.core.logging import record
from app.prompts.itinerary import CUSTOM_REQUEST_PROMPT, PLACE_RETRY_PROMPT, PLACE_SELECTION_PROMPT, PRICE_PROMPT
from app.schemas.itinerary import Place, TravelGenerationRequest
from app.schemas.planner import CustomRequest, ModelSelection, PriceExtraction
from app.services.scheduler import STAY_MINUTES, DayPlan, Visit, validate_selection
from app.services.selection import complete_selection


class OpenAIPlanner:
    def __init__(self, openai: OpenAIClient):
        self.openai = openai

    async def parse_custom_request(self, context: dict) -> CustomRequest:
        messages = [
            {"role": "system", "content": CUSTOM_REQUEST_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        raw = await self.openai.complete_json(messages, CustomRequest.model_json_schema(), "audigo_custom_request", max_tokens=1000)
        try:
            return CustomRequest.model_validate_json(raw)
        except ValidationError as exc:
            raise InvalidModelOutput("custom request must match schema") from exc

    async def select_places(self, context: dict, feedback: str | None) -> ModelSelection:
        messages = [
            {"role": "system", "content": PLACE_SELECTION_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        if feedback:
            messages.append({"role": "user", "content": PLACE_RETRY_PROMPT.format(feedback=feedback)})
        # 날짜와 장소 ID를 enum으로 묶어 모델이 없는 날짜·장소를 만들지 못하게 합니다.
        schema = ModelSelection.model_json_schema()
        dates = [day["date"] for day in context["day_plans"]]
        schema["properties"]["days"].update(minItems=len(dates), maxItems=len(dates))
        schema["$defs"]["SelectionDay"]["properties"]["date"]["enum"] = dates
        schema["$defs"]["SelectionItem"]["properties"]["provider_place_id"]["enum"] = [
            place["provider_place_id"] for place in context["candidates"]
        ]
        raw = await self.openai.complete_json(messages, schema, "audigo_itinerary")
        try:
            return ModelSelection.model_validate_json(raw)
        except ValidationError as exc:
            raise InvalidModelOutput("model output must match itinerary schema") from exc

    async def extract_prices(self, entries: list[dict]) -> PriceExtraction:
        messages = [
            {"role": "system", "content": PRICE_PROMPT},
            {"role": "user", "content": json.dumps(entries, ensure_ascii=False)},
        ]
        schema = PriceExtraction.model_json_schema()
        schema["$defs"]["PlacePrice"]["properties"]["provider_place_id"]["enum"] = [e["provider_place_id"] for e in entries]
        raw = await self.openai.complete_json(messages, schema, "audigo_prices", max_tokens=4000)
        try:
            return PriceExtraction.model_validate_json(raw)
        except ValidationError as exc:
            raise InvalidModelOutput("price output must match schema") from exc


async def interpret_custom_request(planner, request: TravelGenerationRequest, dates: list[str]) -> CustomRequest:
    if not request.preference.extra_request:
        return CustomRequest.empty()
    context = {
        "extra_request": request.preference.extra_request,
        "trip_dates": dates,
        "preference": request.preference.model_dump(exclude={"extra_request"}),
    }
    try:
        custom = await planner.parse_custom_request(context)
    except InvalidModelOutput:
        # 해석 결과가 형식에 맞지 않아도 생성은 계속합니다. 공통 필드는 그대로 적용됩니다.
        record("custom_request_unparsed")
        return CustomRequest.empty()
    custom = custom.model_copy(update={"day_overrides": [o for o in custom.day_overrides if o.day_number <= len(dates)]})
    record("custom_request_parsed", overridden=[
        name for name, value in custom.model_dump().items() if value not in (None, [], "")
    ])
    return custom


def build_selection_context(
    request: TravelGenerationRequest, custom: CustomRequest, plans: list[DayPlan],
    candidates: list[Place], required: list[Place],
) -> dict:
    # [프롬프트] 관광·식당 선택 모델에 user 메시지로 보내는 입력 JSON입니다.
    # 모델에 더 알려줄 정보가 있으면 여기에 키를 추가하고, prompts/itinerary.PLACE_SELECTION_PROMPT에
    # 그 키를 어떻게 쓸지 규칙을 적으세요.
    preference = request.preference
    modes = sorted({p.transport for p in plans})
    return {
        "request": {
            "region": request.region_name,
            "headcount": request.headcount,
            "companion_type": request.companion_type,
            "themes": preference.themes + custom.themes,
            "foods": preference.foods + custom.foods,
            "budget": {"min": preference.budget_min, "max": preference.budget_max, "currency": preference.budget_type},
            "distance_preference": preference.distance_preference,
            "custom_notes": custom.notes,
            "avoid": custom.avoid,
        },
        "day_plans": [p.summary() for p in plans],
        "stay_minutes": STAY_MINUTES,
        "required_order": [p.provider_place_id for p in required if p.category != "숙소"],
        "candidates": [
            {
                "provider_place_id": p.provider_place_id, "place_name": p.place_name, "category": p.category,
                "source_category": p.source_category, "address": p.address,
                "latitude": p.latitude, "longitude": p.longitude, "is_required": p.is_required,
            }
            for p in candidates
        ],
        "travel_minutes": {
            "description": "후보 간 예상 이동시간(분). 행·열 순서는 place_ids",
            "place_ids": [p.provider_place_id for p in candidates],
            "by_transport": {mode: [[travel_minutes(a, b, mode) for b in candidates] for a in candidates] for mode in modes},
        },
    }


async def recommend_places(
    planner, request: TravelGenerationRequest, custom: CustomRequest, plans: list[DayPlan],
    candidates: list[Place], required: list[Place],
) -> tuple[str, list[list[Visit]]]:
    if any(p.meals for p in plans) and not any(c.category == "식당" for c in candidates):
        raise GenerationFailed("식당 후보가 없습니다.", reason="no_restaurant_candidates")
    context = build_selection_context(request, custom, plans, candidates, required)
    feedback = None
    for attempt in range(2):
        try:
            selection = await planner.select_places(context, feedback)
            selection = complete_selection(plans, selection, candidates)
            return selection.title, validate_selection(plans, selection, candidates, required)
        except InvalidModelOutput as exc:
            feedback = str(exc)
            record("place_selection_retry", attempt=attempt + 1, reason=feedback)
    raise GenerationFailed("장소를 추천하지 못했습니다.", reason="place_selection_failed")
