from tests.conftest import AUTH


def metric_line(text: str, prefix: str) -> str | None:
    return next((line for line in text.splitlines() if line.startswith(prefix)), None)


def test_metrics_is_public_prometheus_text(client):
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in response.text


def test_requests_are_counted_by_route_template(client):
    client.get("/health")
    client.get("/nope")

    text = client.get("/metrics").text

    assert metric_line(text, 'http_requests_total{method="GET",path="/health",status="200"}')
    assert metric_line(text, 'http_requests_total{method="GET",path="unmatched",status="404"}')
    assert 'path="/nope"' not in text  # 실제 URL은 라벨로 쓰지 않음
    assert 'path="/metrics"' not in text  # /metrics 자체 호출은 제외


def test_metrics_not_listed_in_schema(client):
    paths = client.app.openapi()["paths"]

    assert "/metrics" not in paths


def test_metrics_ignores_api_token(client):
    assert client.get("/metrics", headers=AUTH).status_code == 200
