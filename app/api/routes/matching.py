"""Synchronous JSON matching with no external API or itinerary dependencies."""
from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.api.deps import get_settings
from app.core.config import Settings
from app.core.exceptions import ApiError, describe_validation_error
from app.core.logging import record
from app.docs.matching_examples import ERROR_RESPONSES, REQUEST_EXAMPLE, RESPONSE_EXAMPLE
from app.schemas.matching import MatchingRequest, MatchingResponse
from app.services.matching import RANKING_VERSION, recommend


class LimitedMatchingRequest(Request):
    """Bound buffered bytes before JSON decoding, including chunked requests."""

    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            chunks = []
            size = 0
            limit = self.app.state.settings.matching_max_body_bytes
            async for chunk in self.stream():
                size += len(chunk)
                if size > limit:
                    raise ApiError(422, "invalid_request", f"body: 최대 {limit}바이트까지 전달할 수 있습니다.",
                                   reason="matching_body_limit")
                chunks.append(chunk)
            self._body = b"".join(chunks)
        return self._body


class MatchingRoute(APIRoute):
    """Keep matching validation at 422 without changing itinerary's 400 contract."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handler(request: Request):
            request = LimitedMatchingRequest(request.scope, request.receive)
            # Check outside FastAPI's parsing block, which translates arbitrary
            # body-read exceptions into HTTP400. Original handler reuses the cache.
            await request.body()
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                descriptions = list(dict.fromkeys(describe_validation_error(e) for e in exc.errors()))[:5]
                raise ApiError(422, "invalid_request", " / ".join(descriptions), reason="matching_validation") from exc

        return handler


router = APIRouter(tags=["matching"], route_class=MatchingRoute)
RequestBody = Annotated[MatchingRequest, Body(openapi_examples={
    "backend": {"summary": "BE가 공급한 실제 후보를 평가", "value": REQUEST_EXAMPLE},
})]


@router.post(
    "/api/ai/v1/matches/candidates",
    response_model=MatchingResponse,
    responses={**ERROR_RESPONSES, 200: {"content": {"application/json": {"example": RESPONSE_EXAMPLE}}}},
    summary="성별·여행 속도 필터와 테마 점수 기반 후보 점수 추천",
    description=(
        "request 내부의 성별 선호·여행 속도로 필터링하고 테마·속도 점수로 최대 3명의 후보 user_id와 match_rate를 반환합니다. "
        "예산은 검증만 하며 추천 점수·필터에는 사용하지 않습니다. "
        "후보 상한은 MATCHING_MAX_CANDIDATES 설정을 따릅니다. 초과 시 자르지 않고 거부합니다."
    ),
)
def recommend_candidates(body: RequestBody, settings: Annotated[Settings, Depends(get_settings)]):
    if len(body.candidates) > settings.matching_max_candidates:
        raise ApiError(
            422, "invalid_request",
            f"candidates: 최대 {settings.matching_max_candidates}명까지 전달할 수 있습니다.",
            reason="matching_candidate_limit",
        )
    started = time.monotonic()
    result = recommend(request_conditions=body.request, candidates=body.candidates, limit=body.limit)
    record(
        "matching_recommendation", matching_request_id=body.request.matching_request_id,
        input_count=len(body.candidates), filtered_count=result.filtered_count, output_count=result.response.count,
        elapsed_ms=round((time.monotonic() - started) * 1000, 3), ranking_version=RANKING_VERSION,
    )
    return result.response
