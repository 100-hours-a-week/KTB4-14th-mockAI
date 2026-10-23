import pytest

from app.core.config import Settings


def test_worker_defaults_preserve_live_serial_processing():
    settings = Settings()
    assert settings.queue_worker_mode == "live"
    assert settings.queue_worker_concurrency == 1
    assert settings.mock_job_delay_seconds == 30.0


def test_worker_settings_are_loaded_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIGO_AI_QUEUE_WORKER_MODE", "mock")
    monkeypatch.setenv("AUDIGO_AI_QUEUE_WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("MOCK_JOB_DELAY_SECONDS", "12.5")
    settings = Settings.from_env(tmp_path / "missing.env")
    assert settings.queue_worker_mode == "mock"
    assert settings.queue_worker_concurrency == 4
    assert settings.mock_job_delay_seconds == 12.5


@pytest.mark.parametrize("mode", ["", "MOCK", "unknown"])
def test_invalid_worker_mode_is_rejected(mode):
    with pytest.raises(RuntimeError, match="AUDIGO_AI_QUEUE_WORKER_MODE"):
        Settings(queue_worker_mode=mode)


@pytest.mark.parametrize("concurrency", [0, -1, 33, 1.5, True])
def test_invalid_worker_concurrency_is_rejected(concurrency):
    with pytest.raises(RuntimeError, match="AUDIGO_AI_QUEUE_WORKER_CONCURRENCY"):
        Settings(queue_worker_concurrency=concurrency)


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_mock_job_delay_is_rejected(delay):
    with pytest.raises(RuntimeError, match="MOCK_JOB_DELAY_SECONDS"):
        Settings(mock_job_delay_seconds=delay)


def test_mock_delay_must_fit_inside_generation_timeout():
    with pytest.raises(RuntimeError, match="MOCK_JOB_DELAY_SECONDS"):
        Settings(queue_worker_mode="mock", mock_job_delay_seconds=300)


@pytest.mark.parametrize("name,value", [
    ("AUDIGO_AI_QUEUE_WORKER_CONCURRENCY", "many"),
    ("MOCK_JOB_DELAY_SECONDS", "slow"),
])
def test_invalid_numeric_environment_value_names_the_setting(monkeypatch, tmp_path, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        Settings.from_env(tmp_path / "missing.env")
