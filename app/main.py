from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request

from app.api.routes import health
from app.core.config import Settings
from app.core.exceptions import register_exception_handlers
from app.core.security import require_api_token


def create_app(*, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    app = FastAPI(title="Audigo Mock AI API", version="0.1.0")

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    register_exception_handlers(app)

    app.include_router(health.router)

    # Mock endpoints that require the backend's Bearer token go here.
    protected = APIRouter(dependencies=[Depends(require_api_token(settings.api_token))])
    app.include_router(protected)

    return app


app = create_app()
