"""(단계, 상태, 결과)를 SSE로 보냅니다. keep-alive와 전체 제한 시간을 처리합니다."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time

from app.core.exceptions import ApiError, ServiceUnavailable
from app.core.logging import failure, record, request_id as log_request_id


def encode_event(name: str, sequence: int, payload: dict) -> str:
    return f"id: {sequence}\nevent: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


KEEP_ALIVE = ": keep-alive\n\n"


async def stage_events(generator, settings, request_id: str):
    """keep-alive는 ("tick", None, None), 단계마다 (단계, 상태, 결과)를 내보냅니다.
    파이프라인에서 예외가 나면 마지막으로 ("error", 실패한 단계, ApiError)를 내보냅니다."""
    token = log_request_id.set(request_id)
    loop = asyncio.get_running_loop()
    started = stage_started = time.monotonic()
    deadline = loop.time() + settings.generation_timeout_seconds
    pending, stage = None, "PLACES"
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ServiceUnavailable()
            if pending is None:
                pending = asyncio.create_task(anext(generator))
            done, _ = await asyncio.wait({pending}, timeout=min(settings.stream_heartbeat_seconds, remaining))
            # 제한 시간이 지난 뒤 도착한 결과는 성공으로 내보내지 않습니다.
            if loop.time() >= deadline:
                raise ServiceUnavailable()
            if not done:
                yield "tick", None, None
                continue
            try:
                stage, status, result = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            if status == "STARTED":
                stage_started = time.monotonic()
            record("generation_stage", stage=stage, status=status,
                   elapsed_ms=round((time.monotonic() - started) * 1000),
                   stage_ms=round((time.monotonic() - stage_started) * 1000))
            yield stage, status, result
    except asyncio.CancelledError:
        raise  # 클라이언트 연결 끊김. 취소되면서 진행 중인 외부 호출도 닫힙니다.
    except Exception as exc:
        failure("pipeline_failed", exc, stage=stage, elapsed_ms=round((time.monotonic() - started) * 1000))
        if not isinstance(exc, ApiError):
            exc = ApiError(500, "internal_server_error", "서버 내부 오류가 발생했습니다.")
        # HTTP 헤더는 이미 나갔으므로 실패는 마지막 SSE 이벤트로 알립니다.
        yield "error", stage, exc
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending
        try:
            await generator.aclose()
        finally:
            log_request_id.reset(token)
