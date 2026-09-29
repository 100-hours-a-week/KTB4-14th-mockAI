def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-Id"].startswith("req_")


def test_unknown_path_uses_error_envelope(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"message": "not_found", "data": None}
