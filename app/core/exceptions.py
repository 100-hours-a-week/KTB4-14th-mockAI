from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import failure, record


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.reason = reason


class ServiceUnavailable(ApiError):
    def __init__(self) -> None:
        super().__init__(503, "ai_service_unavailable", "잠시 후 다시 시도해주세요.")


class RoutingUnavailable(ApiError):
    def __init__(self) -> None:
        super().__init__(503, "routing_service_unavailable", "길찾기 조회에 실패했습니다.")


class GenerationFailed(ApiError):
    def __init__(self, message: str = "일정을 생성하지 못했습니다.", *, reason: str | None = None) -> None:
        super().__init__(422, "ai_itinerary_generation_failed", message, reason=reason)


class InvalidModelOutput(ValueError):
    """모델 재시도(1번)에 넘길 검증 실패 사유. 클라이언트에는 보여주지 않습니다."""


DATETIME_HINT = "날짜·시각 형식이어야 합니다 (예: 2026-09-19T10:00:00)."
SIMPLE_REASONS = {
    "missing": "필수 값입니다.",
    "extra_forbidden": "지원하지 않는 필드입니다.",
    "json_invalid": "JSON 문법이 올바르지 않습니다. 마지막 항목 뒤 쉼표, 따옴표, 괄호를 확인하세요.",
    "int_parsing": "정수여야 합니다.", "int_type": "정수여야 합니다.", "int_from_float": "소수점 없는 정수여야 합니다.",
    "float_parsing": "숫자여야 합니다.", "float_type": "숫자여야 합니다.",
    "string_type": "문자열이어야 합니다.", "list_type": "배열이어야 합니다.",
    "model_type": "객체여야 합니다.", "model_attributes_type": "객체여야 합니다.", "dict_type": "객체여야 합니다.",
    "bool_type": "true 또는 false여야 합니다.", "bool_parsing": "true 또는 false여야 합니다.",
    "datetime_parsing": DATETIME_HINT, "datetime_type": DATETIME_HINT, "datetime_from_date_parsing": DATETIME_HINT,
    "finite_number": "유한한 숫자여야 합니다.",
}
FIELD_MESSAGE = re.compile(r"^([A-Za-z_][\w.\[\]]*): (.+)$", re.S)


def describe_validation_error(error: dict) -> str:
    """Pydantic 오류 하나를 "필드: 이유" 문장으로 바꿉니다. 입력 값은 담지 않습니다."""
    kind, ctx = error["type"], error.get("ctx") or {}
    parts = [part for part in error["loc"] if part != "body"]
    if kind == "json_invalid":
        # JSON 문법 오류의 loc는 필드가 아니라 요청 본문의 글자 위치입니다.
        return f"요청 본문 {parts[0]}번째 글자 근처: {SIMPLE_REASONS[kind]}" if parts else f"body: {SIMPLE_REASONS[kind]}"
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    if kind == "value_error":
        # 스키마 검증 메시지. 필드 사이 관계 규칙은 "필드: 이유" 형식으로 적혀 있습니다.
        message = str(error["msg"]).removeprefix("Value error, ")
        matched = FIELD_MESSAGE.match(message)
        if matched:
            field, message = matched.groups()
            path = f"{path}.{field}" if path else field
    elif kind in SIMPLE_REASONS:
        message = SIMPLE_REASONS[kind]
    elif kind == "greater_than":
        message = f"{ctx.get('gt')}보다 커야 합니다."
    elif kind == "greater_than_equal":
        message = f"{ctx.get('ge')} 이상이어야 합니다."
    elif kind == "less_than":
        message = f"{ctx.get('lt')}보다 작아야 합니다."
    elif kind == "less_than_equal":
        message = f"{ctx.get('le')} 이하여야 합니다."
    elif kind == "string_too_short":
        message = f"최소 {ctx.get('min_length')}자 이상이어야 합니다."
    elif kind == "string_too_long":
        message = f"최대 {ctx.get('max_length')}자까지 입력할 수 있습니다."
    elif kind == "too_short":
        message = f"최소 {ctx.get('min_length')}개 이상이어야 합니다."
    elif kind == "too_long":
        message = f"최대 {ctx.get('max_length')}개까지 넣을 수 있습니다."
    elif kind == "string_pattern_mismatch":
        message = "대문자 3글자 통화 코드여야 합니다 (예: KRW)." if path.endswith("budget_type") else "형식이 올바르지 않습니다."
    elif kind == "literal_error":
        expected = str(ctx.get("expected", "")).replace("'", "").replace(" or ", ", ")
        message = f"{expected} 중 하나여야 합니다."
    else:
        message = "값이 올바르지 않습니다."
    return f"{path or 'body'}: {message}"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        failure("request_failed", exc, http_status=exc.status_code)
        data = None
        if exc.status_code in (400, 422, 503):
            data = {"error_message": exc.message}
            if exc.status_code == 422 and hasattr(request.state, "travel_plan_id"):
                data = {"travel_plan_id": request.state.travel_plan_id, **data}
        return JSONResponse(status_code=exc.status_code, content={"message": exc.code, "data": data})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        record("request_invalid", http_status=400, errors=[
            {"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in errors[:10]
        ])
        # 입력 값이나 요청 본문은 응답에 다시 담지 않습니다.
        descriptions = list(dict.fromkeys(describe_validation_error(e) for e in errors))[:5]
        return JSONResponse(
            status_code=400,
            content={"message": "invalid_request", "data": {"error_message": " / ".join(descriptions)}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = {401: "unauthorized", 404: "not_found", 503: "ai_service_unavailable"}.get(exc.status_code, "http_error")
        data = {"error_message": "잠시 후 다시 시도해주세요."} if exc.status_code == 503 else None
        return JSONResponse(status_code=exc.status_code, headers=exc.headers, content={"message": code, "data": data})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        failure("unexpected_error", exc)
        return JSONResponse(status_code=500, content={"message": "internal_server_error", "data": None})
