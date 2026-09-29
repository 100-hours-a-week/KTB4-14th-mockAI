"""목업 데이터 경로: 실제 스트림과 같은 형식이며 외부 API를 호출하지 않습니다."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_mock_scenario, get_settings
from app.api.routes.itinerary import SSE_HEADERS
from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable
from app.docs.openapi_examples import ERROR_RESPONSES, REQUEST_EXAMPLES, STREAM_RESPONSE
from app.schemas.itinerary import TravelGenerationRequest
from app.services.mock_generator import mock_stages
from app.services.streaming.backend_adapter import backend_stream


router = APIRouter(prefix="/mock", tags=["mock"])


@router.post("/api/ai/v1/itinerary-jobs/stream", response_class=StreamingResponse,
             responses={**ERROR_RESPONSES, 200: STREAM_RESPONSE}, summary="목업 일정 생성 (SSE)",
             description="외부 API를 호출하지 않고 가짜 일정을 생성합니다. X-Mock-Scenario 헤더(없으면 MOCK_SCENARIO)로 실패를 재현합니다.")
async def stream_mock_itinerary(
    body: Annotated[TravelGenerationRequest, Body(openapi_examples=REQUEST_EXAMPLES)],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    scenario: Annotated[str, Depends(get_mock_scenario)],
):
    request.state.travel_plan_id = body.travel_plan_id
    if scenario == "unavailable":
        raise ServiceUnavailable()
    return StreamingResponse(
        backend_stream(mock_stages(body, scenario, settings.mock_stage_delay_seconds), settings,
                       request.state.request_id, body.travel_plan_id),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )
