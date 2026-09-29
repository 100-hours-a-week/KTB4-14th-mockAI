import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.conftest import TOKEN


DOCS_PATHS = ["/docs", "/redoc", "/openapi.json"]


def basic(username: str, password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


@pytest.fixture
def guarded():
    settings = Settings(api_token=TOKEN, docs_username="admin", docs_password="docs-password")
    with TestClient(create_app(settings=settings)) as client:
        yield client


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_require_basic_credentials(guarded, path):
    response = guarded.get(path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_wrong_credentials_are_rejected(guarded, path):
    assert guarded.get(path, headers=basic("admin", "wrong")).status_code == 401
    assert guarded.get(path, headers=basic("root", "docs-password")).status_code == 401


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_valid_credentials_open_the_docs(guarded, path):
    assert guarded.get(path, headers=basic("admin", "docs-password")).status_code == 200


def test_docs_are_disabled_without_credentials_configured(client):
    for path in DOCS_PATHS:
        assert client.get(path).status_code == 404


def test_docs_routes_are_not_listed_in_the_schema(guarded):
    paths = guarded.get("/openapi.json", headers=basic("admin", "docs-password")).json()["paths"]

    assert not set(DOCS_PATHS) & set(paths)
