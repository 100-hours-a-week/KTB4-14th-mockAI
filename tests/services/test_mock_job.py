import asyncio

import pytest

from app.core.exceptions import ServiceUnavailable
from app.services.mock_generator import mock_job_stages, mock_stages
from app.services.pipeline import run_to_completion
from tests.conftest import request_model


def test_mock_job_preserves_existing_pipeline_events_and_result():
    async def run():
        expected = [event async for event in mock_stages(request_model(), "success", 0)]
        actual = [event async for event in mock_job_stages(request_model(), "success", 0)]
        assert actual == expected

    asyncio.run(run())


def test_mock_job_delay_is_total_and_does_not_block_the_event_loop():
    async def run():
        first_progress = asyncio.Event()
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def consume():
            async for stage, state, _ in mock_job_stages(request_model(), "success", 0.05):
                if stage == "PLACES" and state == "STARTED":
                    first_progress.set()

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(first_progress.wait(), timeout=2)
            await asyncio.sleep(0)
            assert not task.done()
            await asyncio.wait_for(task, timeout=2)
            assert loop.time() - started >= 0.05
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_mock_job_unavailable_scenario_does_not_generate_a_result():
    with pytest.raises(ServiceUnavailable):
        asyncio.run(run_to_completion(mock_job_stages(request_model(), "unavailable", 0)))
