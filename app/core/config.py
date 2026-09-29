from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MOCK_SCENARIOS = ("success", "fail_place", "fail_stay", "fail_route", "unavailable", "drop")


@dataclass(frozen=True)
class Settings:
    api_token: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    kakao_rest_api_key: str | None = field(default=None, repr=False)
    openai_model: str = "gpt-4o-mini"
    model_timeout_seconds: float = 60.0
    kakao_timeout_seconds: float = 10.0
    routing_timeout_seconds: float = 10.0
    # 전체 생성(모든 단계)은 5분 안에 끝나야 합니다.
    generation_timeout_seconds: float = 300.0
    stream_heartbeat_seconds: float = 10.0
    # /mock 경로 전용
    mock_stage_delay_seconds: float = 1.0
    mock_scenario: str = "success"

    @classmethod
    def from_env(cls, env_file: Path = ENV_FILE) -> "Settings":
        # 환경변수가 .env보다 우선합니다. 실행 위치(cwd)와 상관없이 프로젝트 .env를 읽습니다.
        values = {**dotenv_values(env_file), **os.environ}
        token = values.get("AUDIGO_API_TOKEN")
        if token and len(token) < 32:
            raise RuntimeError("AUDIGO_API_TOKEN must be at least 32 characters")
        scenario = values.get("MOCK_SCENARIO") or "success"
        if scenario not in MOCK_SCENARIOS:
            raise RuntimeError(f"MOCK_SCENARIO must be one of {', '.join(MOCK_SCENARIOS)}")
        return cls(
            api_token=token,
            openai_api_key=values.get("OPENAI_API_KEY") or values.get("OPEN_API_KEY"),
            kakao_rest_api_key=values.get("KAKAO_REST_API_KEY"),
            openai_model=values.get("OPENAI_MODEL") or "gpt-4o-mini",
            mock_stage_delay_seconds=float(values.get("MOCK_STAGE_DELAY_SECONDS") or 1.0),
            mock_scenario=scenario,
        )
