"""실제 경로: OpenAI와 카카오를 실제로 호출합니다."""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_providers, get_settings, require_live_keys
from app.core.config import Settings
from app.core.exceptions import ServiceUnavailable
from app.docs.openapi_examples import ERROR_RESPONSES, REQUEST_EXAMPLES, STREAM_RESPONSE
from app.schemas.itinerary import ItineraryResponse, TravelGenerationRequest
from app.services.pipeline import Providers, generation_stages, run_to_completion
from app.services.streaming.backend_adapter import backend_stream


router = APIRouter(tags=["itinerary"], dependencies=[Depends(require_live_keys)])
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
RequestBody = Annotated[TravelGenerationRequest, Body(openapi_examples=REQUEST_EXAMPLES)]


@router.post("/internal/ai/itineraries/generate", response_model=ItineraryResponse, responses=ERROR_RESPONSES,
             summary="일정 생성 (JSON)")
async def generate_itinerary(
    body: RequestBody, request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    providers: Annotated[Providers, Depends(get_providers)],
):
    request.state.travel_plan_id = body.travel_plan_id
    try:
        async with asyncio.timeout(settings.generation_timeout_seconds):
            return await run_to_completion(generation_stages(body, providers))
    except TimeoutError as exc:
        raise ServiceUnavailable() from exc


@router.post("/api/ai/v1/itinerary-jobs/stream", response_class=StreamingResponse,
             responses={**ERROR_RESPONSES, 200: STREAM_RESPONSE}, summary="일정 생성 (SSE)")
async def stream_itinerary(
    body: RequestBody, request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    providers: Annotated[Providers, Depends(get_providers)],
):
    request.state.travel_plan_id = body.travel_plan_id
    return StreamingResponse(
        backend_stream(generation_stages(body, providers), settings, request.state.request_id, body.travel_plan_id),
        media_type="text/event-stream", headers=SSE_HEADERS,
    )
