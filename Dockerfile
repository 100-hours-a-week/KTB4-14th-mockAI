# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /bin/uv

# 의존성 레이어: pyproject.toml/uv.lock이 바뀔 때만 다시 설치
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev



# 테스트 단계: docker build --target test . 로 실행할 때만 빌드됩니다.
FROM builder AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra dev

COPY tests ./tests
RUN /app/.venv/bin/pytest


FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system fastapi \
    && useradd --system --gid fastapi --create-home fastapi

COPY --from=builder --chown=fastapi:fastapi /app/.venv ./.venv
COPY --from=builder --chown=fastapi:fastapi /app/app ./app

USER fastapi
EXPOSE 8000

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
