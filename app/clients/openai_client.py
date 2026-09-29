from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.exceptions import GenerationFailed, InvalidModelOutput, ServiceUnavailable


OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def complete_json(self, messages: list[dict], schema: dict, name: str, *, max_tokens: int = 8000) -> str:
        """strict JSON Schema로 형식을 강제한 모델 응답(JSON 문자열)을 돌려줍니다."""
        if not self.settings.openai_api_key:
            raise ServiceUnavailable()
        try:
            response = await self.client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                timeout=self.settings.model_timeout_seconds,
                # [프롬프트] 모델 호출 파라미터. 모델명은 .env의 OPENAI_MODEL로 바꿉니다.
                json={
                    "model": self.settings.openai_model,
                    "messages": messages,
                    "max_completion_tokens": max_tokens,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": name, "strict": True, "schema": schema},
                    },
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # 외부 API의 응답 본문·인증 정보·헤더는 호출자에게 절대 전달하지 않습니다.
            raise ServiceUnavailable() from exc
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise GenerationFailed("일정을 생성할 수 없는 조건입니다.", reason="model_refusal")
            if choice.get("finish_reason") != "stop" or not isinstance(message.get("content"), str):
                raise InvalidModelOutput("model response is incomplete")
            return message["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, InvalidModelOutput):
                raise
            # 모델 원문에는 사용자 입력이 섞일 수 있어 그대로 노출하지 않습니다.
            raise InvalidModelOutput("model output must be complete JSON") from exc
