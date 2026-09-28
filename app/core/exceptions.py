from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def register_exception_handlers(app: FastAPI) -> None:
    # Same {"message", "data"} envelope as the real AI server.
    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        descriptions = [
            f"{'.'.join(str(part) for part in error['loc'] if part != 'body') or 'body'}: {error['msg']}"
            for error in exc.errors()[:5]
        ]
        return JSONResponse(
            status_code=400,
            content={"message": "invalid_request", "data": {"error_message": " / ".join(descriptions)}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        code = {
            401: "unauthorized",
            404: "not_found",
            503: "ai_service_unavailable",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={"message": code, "data": None},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"message": "internal_server_error", "data": None},
        )
