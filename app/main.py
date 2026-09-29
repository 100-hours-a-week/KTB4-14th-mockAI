from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.api.routes import health, itinerary, mock
from app.clients.kakao_places import KakaoPlaces
from app.clients.kakao_routes import KakaoRoutes
from app.clients.kakao_search import KakaoSearch
from app.clients.openai_client import OpenAIClient
from app.core.config import Settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import request_id as log_request_id
from app.core.security import require_api_token, require_docs_credentials
from app.services.pipeline import Providers
from app.services.planner import OpenAIPlanner


def live_providers(client: httpx.AsyncClient, settings: Settings) -> Providers:
    return Providers(
        places=KakaoPlaces(client, settings),
        routes=KakaoRoutes(client, settings),
        search=KakaoSearch(client, settings),
        planner=OpenAIPlanner(OpenAIClient(client, settings)),
    )


def create_app(*, settings: Settings | None = None, providers: Providers | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient() as client:
            app.state.providers = providers or live_providers(client, settings)
            yield

    # 기본 문서 경로는 끄고, 아래에서 Basic 인증을 붙여 다시 등록합니다.
    app = FastAPI(title="Audigo Itinerary AI API", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        token = log_request_id.set(request.state.request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        finally:
            log_request_id.reset(token)

    register_exception_handlers(app)

    auth = [Depends(require_api_token(settings.api_token))]
    app.include_router(health.router)
    app.include_router(itinerary.router, dependencies=auth)
    app.include_router(mock.router, dependencies=auth)
    if settings.docs_username and settings.docs_password:
        register_docs(app, settings)
    return app


def register_docs(app: FastAPI, settings: Settings) -> None:
    guard = [Depends(require_docs_credentials(settings.docs_username, settings.docs_password))]

    @app.get("/openapi.json", include_in_schema=False, dependencies=guard)
    async def openapi_schema():
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=guard)
    async def swagger_ui():
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/redoc", include_in_schema=False, dependencies=guard)
    async def redoc():
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


app = create_app()
