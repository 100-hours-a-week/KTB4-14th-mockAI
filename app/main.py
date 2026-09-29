from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Request

from app.api.routes import health, itinerary, mock
from app.clients.kakao_places import KakaoPlaces
from app.clients.kakao_routes import KakaoRoutes
from app.clients.kakao_search import KakaoSearch
from app.clients.openai_client import OpenAIClient
from app.core.config import Settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import request_id as log_request_id
from app.core.security import require_api_token
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

    app = FastAPI(title="Audigo Itinerary AI API", version="0.1.0", lifespan=lifespan)
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
    return app


app = create_app()
