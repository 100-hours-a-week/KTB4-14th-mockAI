"""Prometheus 메트릭 정의와 수집 미들웨어.

메트릭 객체는 모듈 로드 시 한 번만 기본 레지스트리에 등록합니다.
(create_app()이 여러 번 호출돼도 중복 등록 오류가 나지 않도록)
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.routing import Match

# 수집 대상에서 제외할 경로. /metrics 자체 호출은 기록하지 않음
EXCLUDED_PATHS = {"/metrics"}

REQUESTS = Counter(
    "http_requests_total",
    "처리한 HTTP 요청 수",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(응답 헤더 반환까지). 스트리밍 응답은 본문 전송 시간 미포함",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "처리 중인 HTTP 요청 수",
    ["method"],
)


def route_path(request: Request) -> str:
    """실제 URL 대신 라우트 템플릿(/items/{id})을 라벨로 사용해 시계열 수가 늘어나지 않게 합니다."""
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return "unmatched"  # 404 등 등록되지 않은 경로는 하나로 묶음


def setup_metrics(app: FastAPI) -> None:
    """요청 메트릭 미들웨어와 GET /metrics 엔드포인트를 등록합니다. (인증 없음, 클러스터 내부 수집용)"""

    @app.middleware("http")
    async def collect_metrics(request: Request, call_next):
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        method = request.method
        path = route_path(request)
        status = "500"  # 예외로 응답이 만들어지지 않은 경우
        IN_PROGRESS.labels(method).inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            LATENCY.labels(method, path).observe(time.perf_counter() - start)
            REQUESTS.labels(method, path, status).inc()
            IN_PROGRESS.labels(method).dec()

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
