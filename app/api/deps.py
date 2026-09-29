from __future__ import annotations

from fastapi import Header, Request

from app.core.config import MOCK_SCENARIOS, Settings
from app.core.exceptions import ApiError, ServiceUnavailable
from app.services.pipeline import Providers


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_providers(request: Request) -> Providers:
    return request.app.state.providers


def require_live_keys(request: Request) -> None:
    settings = get_settings(request)
    if not settings.openai_api_key or not settings.kakao_rest_api_key:
        raise ServiceUnavailable()


def get_mock_scenario(
    request: Request,
    x_mock_scenario: str | None = Header(default=None, description="success | fail_place | fail_stay | fail_route | unavailable | drop"),
) -> str:
    # 헤더 값이 환경변수 MOCK_SCENARIO보다 우선합니다.
    scenario = x_mock_scenario or get_settings(request).mock_scenario
    if scenario not in MOCK_SCENARIOS:
        raise ApiError(400, "invalid_request", "X-Mock-Scenario 값이 올바르지 않습니다.")
    return scenario
