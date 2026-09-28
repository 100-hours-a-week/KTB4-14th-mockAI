from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health():
    client = TestClient(create_app(settings=Settings(api_token="x" * 32)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "mock"}
    assert response.headers["X-Request-Id"].startswith("req_")


def test_unknown_path_uses_error_envelope():
    client = TestClient(create_app(settings=Settings(api_token="x" * 32)))

    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"message": "not_found", "data": None}
