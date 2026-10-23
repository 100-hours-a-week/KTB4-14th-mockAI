"""SQS 큐 워커: Job Queue에서 여행 생성 요청을 받아 진행 상태와 결과를 Result Queue로 보냅니다.

메시지 형식은 백엔드 계약(docs/travel-generation-sqs-contract.md)을 따릅니다. 설계는 docs/design/sqs-queue-worker.md에 있습니다.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import aclosing, suppress
from datetime import datetime, timezone
import json
import logging
from uuid import uuid4

from pydantic import ValidationError

from app.clients.sqs import SqsQueue, sqs_client
from app.core.config import Settings
from app.core.exceptions import ApiError, ServiceUnavailable, describe_validation_error
from app.core.logging import failure, record, request_id as log_request_id
from app.schemas.itinerary import TravelGenerationRequest
from app.services.mock_generator import mock_job_stages
from app.services.pipeline import Providers, generation_stages
from app.services.streaming.backend_adapter import STAGE_NAMES, backend_result
from app.services.streaming.sse import stage_events


MESSAGE_VERSION = "1"
# 처리한 작업 ID를 기억하는 개수. AI 서버는 한 대라서 프로세스 메모리로 중복을 막습니다.
SEEN_LIMIT = 1000
PUBLISH_ATTEMPTS = 3
PUBLISH_RETRY_SECONDS = 1.0
RECEIVE_RETRY_SECONDS = 5.0


def result_message(job_id: int, type_: str, *, stage: str | None = None, state: str | None = None,
                   payload: dict | None = None, error_message: str | None = None) -> dict:
    return {
        "event_id": str(uuid4()), "generation_job_id": job_id, "message_version": MESSAGE_VERSION,
        "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": type_, "stage": stage, "state": state, "payload": payload, "error_message": error_message,
    }


def failed_message(job_id: int, error: ApiError) -> dict:
    # 백엔드는 error_message를 실패 알림에 그대로 씁니다. 사용자가 바꿔 볼 입력을 알려주는 guide를 우선합니다.
    return result_message(job_id, "FAILED", error_message=error.guide or error.message)


def invalid_request(exc: ValidationError) -> ApiError:
    descriptions = list(dict.fromkeys(describe_validation_error(e) for e in exc.errors()))[:5]
    return ApiError(400, "invalid_request", " / ".join(descriptions))


class QueueWorker:
    def __init__(self, jobs: SqsQueue, results: SqsQueue, stages, settings: Settings) -> None:
        self.jobs = jobs
        self.results = results
        self.stages = stages  # TravelGenerationRequest를 받아 (단계, 상태, 결과)를 내보내는 함수
        self.settings = settings
        self.seen: OrderedDict[int, None] = OrderedDict()

    async def run(self) -> None:
        record("queue_worker_started", mode=self.settings.queue_worker_mode,
               concurrency=self.settings.queue_worker_concurrency)
        consumers = [asyncio.create_task(self.consume()) for _ in range(self.settings.queue_worker_concurrency)]
        try:
            await asyncio.gather(*consumers)
        finally:
            for consumer in consumers:
                consumer.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)

    async def consume(self) -> None:
        while True:
            try:
                messages = await self.jobs.receive()
            except Exception as exc:
                failure("queue_receive_failed", exc)
                await asyncio.sleep(RECEIVE_RETRY_SECONDS)
                continue
            for message in messages:
                try:
                    await self.handle(message)
                except Exception as exc:
                    # 메시지 하나의 오류로 워커가 멈추지 않게 합니다.
                    failure("queue_job_unexpected_error", exc, sqs_message_id=message.get("MessageId"))

    async def handle(self, message: dict) -> None:
        job_id = self.job_id(message["Body"])
        if job_id is None:
            # 결과를 보낼 대상이 없습니다. 남겨 두면 보존 기간 동안 계속 다시 들어옵니다.
            record("queue_job_unreadable", level=logging.WARNING, sqs_message_id=message.get("MessageId"))
            await self.delete(message)
            return
        token = log_request_id.set(f"job_{job_id}")
        try:
            if job_id in self.seen:
                record("queue_job_duplicate", generation_job_id=job_id, sqs_message_id=message.get("MessageId"))
                await self.delete(message)
                return
            self.remember(job_id)
            final = await self.generate(job_id, json.loads(message["Body"]))
            if await self.publish_final(final):
                await self.delete(message)
        finally:
            log_request_id.reset(token)

    @staticmethod
    def job_id(body: str) -> int | None:
        try:
            value = json.loads(body)["generation_job_id"]
        except (ValueError, TypeError, KeyError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def remember(self, job_id: int) -> None:
        self.seen[job_id] = None
        if len(self.seen) > SEEN_LIMIT:
            self.seen.popitem(last=False)

    async def generate(self, job_id: int, data: dict) -> dict:
        """진행 상태는 바로 발행하고, 최종 메시지(COMPLETED / FAILED)는 돌려줍니다."""
        try:
            if data.get("message_version") != MESSAGE_VERSION:
                raise ApiError(400, "unsupported_message_version", "지원하지 않는 메시지 버전입니다.")
            request = TravelGenerationRequest.model_validate(data.get("request"))
        except ValidationError as exc:
            record("queue_job_invalid", level=logging.WARNING, generation_job_id=job_id,
                   errors=[{"field": ".".join(map(str, e["loc"])), "type": e["type"]} for e in exc.errors()[:10]])
            return failed_message(job_id, invalid_request(exc))
        except ApiError as exc:
            failure("queue_job_invalid", exc, generation_job_id=job_id)
            return failed_message(job_id, exc)

        record("queue_job_started", generation_job_id=job_id, travel_plan_id=request.travel_plan_id)
        async with aclosing(stage_events(self.stages(request), self.settings, f"job_{job_id}")) as events:
            async for stage, status, result in events:
                if stage == "tick":
                    continue
                if stage == "error":
                    return failed_message(job_id, result)
                if stage == "COMPLETE":
                    return result_message(job_id, "COMPLETED", stage="ROUTE_OPTIMIZE", state="DONE",
                                          payload=backend_result(result))
                # 길찾기 완료는 COMPLETED가 대신합니다. SSE의 ROUTE_OPTIMIZE_DONE과 같습니다.
                if stage == "ROUTES" and status == "COMPLETED":
                    continue
                await self.publish_progress(job_id, STAGE_NAMES[stage], "RUNNING" if status == "STARTED" else "DONE")
        # 최종 일정 없이 생성이 끝났습니다.
        return failed_message(job_id, ServiceUnavailable())

    async def publish_progress(self, job_id: int, stage: str, state: str) -> None:
        try:
            await self.results.send(result_message(job_id, "PROGRESS", stage=stage, state=state))
        except Exception as exc:
            # 진행 상태는 놓쳐도 최종 결과로 복구되므로 생성을 계속합니다.
            failure("queue_progress_publish_failed", exc, generation_job_id=job_id, stage=stage, state=state)

    async def publish_final(self, message: dict) -> bool:
        for attempt in range(1, PUBLISH_ATTEMPTS + 1):
            try:
                await self.results.send(message)
            except Exception as exc:
                failure("queue_result_publish_failed", exc, generation_job_id=message["generation_job_id"],
                        type=message["type"], attempt=attempt)
                if attempt < PUBLISH_ATTEMPTS:
                    await asyncio.sleep(PUBLISH_RETRY_SECONDS)
                continue
            record("queue_job_finished", generation_job_id=message["generation_job_id"], type=message["type"])
            return True
        # Job 메시지를 남겨 둡니다. 다시 들어오면 처리 기록에 걸려 삭제되고, 백엔드 watchdog이 작업을 실패 처리합니다.
        return False

    async def delete(self, message: dict) -> None:
        try:
            await self.jobs.delete(message["ReceiptHandle"])
        except Exception as exc:
            # 다시 들어오면 처리 기록에 걸려 삭제됩니다.
            failure("queue_job_delete_failed", exc, sqs_message_id=message.get("MessageId"))


def start_queue_worker(settings: Settings, providers: Providers) -> asyncio.Task | None:
    if settings.queue_worker_mode == "off" or not (settings.job_queue_url and settings.result_queue_url):
        record("queue_worker_disabled", mode=settings.queue_worker_mode)
        return None
    client = sqs_client(settings.aws_region)
    if settings.queue_worker_mode == "mock":
        stages = lambda request: mock_job_stages(request, settings.mock_scenario, settings.mock_job_delay_seconds)
    else:
        stages = lambda request: generation_stages(request, providers)
    worker = QueueWorker(SqsQueue(client, settings.job_queue_url), SqsQueue(client, settings.result_queue_url),
                         stages, settings)
    return asyncio.create_task(worker.run())


async def stop_queue_worker(task: asyncio.Task | None) -> None:
    # 진행 중인 생성도 취소합니다. 삭제되지 않은 Job 메시지는 Visibility Timeout 뒤 다시 보입니다.
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
