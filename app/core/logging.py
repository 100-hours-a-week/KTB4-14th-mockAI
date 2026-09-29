"""안전한 운영 로그: 요청 본문, 인증 정보, 외부 API 오류 원문은 남기지 않습니다."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import time
import traceback

request_id = ContextVar("request_id", default="unknown")
logger = logging.getLogger("uvicorn.error.audigo")


def record(event: str, *, level: int = logging.INFO, **fields) -> None:
    logger.log(level, json.dumps({"event": event, "request_id": request_id.get(), **fields}, ensure_ascii=False, default=str))


def failure(event: str, exc: Exception, **fields) -> None:
    # 발생 위치는 남기고, 예외 메시지·연결된 HTTP 오류는 키가 샐 수 있어 남기지 않습니다.
    frames = [{"file": frame.filename.rsplit("/", 1)[-1], "line": frame.lineno, "function": frame.name}
              for frame in traceback.extract_tb(exc.__traceback__)]
    record(event, level=logging.ERROR, error_type=type(exc).__name__,
           code=getattr(exc, "code", "internal_server_error"),
           reason=getattr(exc, "reason", None), frames=frames, **fields)


@contextmanager
def timed(step: str, **fields):
    """단계 안쪽 작업의 소요 시간(ms)을 step_timing 로그로 남깁니다."""
    started = time.monotonic()
    try:
        yield
    finally:
        record("step_timing", step=step, elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
