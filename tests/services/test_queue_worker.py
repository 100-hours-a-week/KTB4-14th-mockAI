import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.exceptions import GUIDE_NO_ACCOMMODATION
from app.main import create_app
from app.services import queue_worker
from app.services.mock_generator import mock_stages
from app.services.queue_worker import QueueWorker
from tests.conftest import TOKEN, body


class FakeQueue:
    def __init__(self, fail_sends: int = 0, fail_deletes: bool = False) -> None:
        self.sent: list[dict] = []
        self.deleted: list[str] = []
        self.fail_sends = fail_sends
        self.fail_deletes = fail_deletes

    async def send(self, message: dict) -> None:
        json.dumps(message)  # 실제 큐처럼 JSON으로 직렬화할 수 있어야 합니다.
        if message["type"] != "PROGRESS" and self.fail_sends:
            self.fail_sends -= 1
            raise RuntimeError("send failed")
        self.sent.append(message)

    async def delete(self, receipt_handle: str) -> None:
        if self.fail_deletes:
            raise RuntimeError("delete failed")
        self.deleted.append(receipt_handle)


def job(job_id=41, receipt="r1", **overrides) -> dict:
    payload = {"event_id": "e1", "generation_job_id": job_id, "message_version": "1",
               "occurred_at": "2026-10-16T01:00:00Z", "request": body(), **overrides}
    return {"MessageId": f"m-{receipt}", "ReceiptHandle": receipt, "Body": json.dumps(payload)}


def worker(results=None, jobs=None, scenario="success", settings=None, stages=None):
    calls = []

    def run(request):
        calls.append(request.travel_plan_id)
        return stages(request) if stages else mock_stages(request, scenario, 0)

    settings = settings or Settings(mock_stage_delay_seconds=0)
    built = QueueWorker(jobs or FakeQueue(), results or FakeQueue(), run, settings)
    return built, calls


@pytest.fixture(autouse=True)
def no_publish_wait(monkeypatch):
    monkeypatch.setattr(queue_worker, "PUBLISH_RETRY_SECONDS", 0)


def test_publishes_progress_then_completed_and_deletes_job():
    w, _ = worker()
    asyncio.run(w.handle(job()))

    sent = w.results.sent
    assert [(m["type"], m["stage"], m["state"]) for m in sent] == [
        ("PROGRESS", "PLACE_RECOMMEND", "RUNNING"), ("PROGRESS", "PLACE_RECOMMEND", "DONE"),
        ("PROGRESS", "STAY_RECOMMEND", "RUNNING"), ("PROGRESS", "STAY_RECOMMEND", "DONE"),
        ("PROGRESS", "ROUTE_OPTIMIZE", "RUNNING"), ("COMPLETED", "ROUTE_OPTIMIZE", "DONE"),
    ]
    assert all(m["generation_job_id"] == 41 and m["message_version"] == "1" for m in sent)
    assert len({m["event_id"] for m in sent}) == len(sent)
    assert sent[-1]["occurred_at"].endswith("Z")
    payload = sent[-1]["payload"]
    assert [day["day_number"] for day in payload["days"]] == [1, 2, 3]
    assert "title" in payload
    assert w.jobs.deleted == ["r1"]


def test_duplicate_job_is_deleted_without_generating_again():
    w, calls = worker()
    asyncio.run(w.handle(job(receipt="r1")))
    asyncio.run(w.handle(job(receipt="r2")))

    assert calls == [10]
    assert [m["type"] for m in w.results.sent].count("COMPLETED") == 1
    assert w.jobs.deleted == ["r1", "r2"]


def test_seen_jobs_are_bounded(monkeypatch):
    monkeypatch.setattr(queue_worker, "SEEN_LIMIT", 2)
    w, _ = worker()
    for job_id in (1, 2, 3):
        w.remember(job_id)
    assert list(w.seen) == [2, 3]


def test_generation_failure_publishes_guide_and_deletes_job():
    w, _ = worker(scenario="fail_stay")
    asyncio.run(w.handle(job()))

    final = w.results.sent[-1]
    assert final["type"] == "FAILED"
    assert final["error_message"] == GUIDE_NO_ACCOMMODATION
    assert final["payload"] is None
    assert w.jobs.deleted == ["r1"]


def test_stream_ending_without_itinerary_is_failed():
    w, _ = worker(scenario="drop")
    asyncio.run(w.handle(job()))

    assert w.results.sent[-1]["type"] == "FAILED"
    assert w.results.sent[-1]["error_message"] == "잠시 후 다시 시도해주세요."


def test_generation_timeout_is_failed():
    async def slow(request):
        yield "PLACES", "STARTED", None
        await asyncio.sleep(1)

    settings = Settings(generation_timeout_seconds=0.05, stream_heartbeat_seconds=0.01)
    w, _ = worker(settings=settings, stages=slow)
    asyncio.run(w.handle(job()))

    assert [m["type"] for m in w.results.sent] == ["PROGRESS", "FAILED"]
    assert w.jobs.deleted == ["r1"]


def test_unreadable_message_is_deleted_without_result():
    w, calls = worker()
    for raw in ("not json", "[]", json.dumps({"generation_job_id": "41"}), json.dumps({"generation_job_id": True})):
        asyncio.run(w.handle({"MessageId": "m", "ReceiptHandle": "r", "Body": raw}))

    assert calls == []
    assert w.results.sent == []
    assert w.jobs.deleted == ["r"] * 4


def test_invalid_request_publishes_failed():
    w, calls = worker()
    asyncio.run(w.handle(job(request=body(headcount=0))))

    assert calls == []
    final = w.results.sent[-1]
    assert final["type"] == "FAILED"
    assert final["error_message"].startswith("headcount:")
    assert w.jobs.deleted == ["r1"]


def test_unsupported_version_publishes_failed():
    w, calls = worker()
    asyncio.run(w.handle(job(message_version="2")))

    assert calls == []
    assert w.results.sent[-1]["type"] == "FAILED"
    assert w.jobs.deleted == ["r1"]


def test_final_publish_is_retried():
    w, _ = worker(results=FakeQueue(fail_sends=2))
    asyncio.run(w.handle(job()))

    assert w.results.sent[-1]["type"] == "COMPLETED"
    assert w.jobs.deleted == ["r1"]


def test_job_is_kept_when_final_publish_keeps_failing():
    w, calls = worker(results=FakeQueue(fail_sends=3))
    asyncio.run(w.handle(job(receipt="r1")))

    assert "COMPLETED" not in [m["type"] for m in w.results.sent]
    assert w.jobs.deleted == []

    # 다시 들어오면 AI를 호출하지 않고 삭제합니다.
    asyncio.run(w.handle(job(receipt="r2")))
    assert calls == [10]
    assert w.jobs.deleted == ["r2"]


def test_delete_failure_does_not_raise():
    w, _ = worker(jobs=FakeQueue(fail_deletes=True))
    asyncio.run(w.handle(job()))

    assert w.results.sent[-1]["type"] == "COMPLETED"


def test_run_survives_receive_failure(monkeypatch):
    monkeypatch.setattr(queue_worker, "RECEIVE_RETRY_SECONDS", 0)

    class Jobs(FakeQueue):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("receive failed")
            if self.calls == 2:
                return [job()]
            raise asyncio.CancelledError

    w, calls = worker(jobs=Jobs())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w.run())
    assert calls == [10]
    assert w.jobs.deleted == ["r1"]


def test_worker_is_not_started_without_queue_urls():
    app = create_app(settings=Settings(api_token=TOKEN, openai_api_key="test", kakao_rest_api_key="test"))
    with TestClient(app):
        assert app.state.queue_worker is None
