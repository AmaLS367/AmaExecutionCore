from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.main import create_app
from backend.task_utils import create_logged_task


class _PassiveRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return None


class _MonitoringPositionManager:
    def __init__(self) -> None:
        self.run_calls: list[float] = []
        self.stop_calls = 0
        self._stop_event = asyncio.Event()

    async def run_spot_exit_monitor(self, *, poll_interval_seconds: float) -> None:
        self.run_calls.append(poll_interval_seconds)
        await self._stop_event.wait()

    def stop_spot_exit_monitor(self) -> None:
        self.stop_calls += 1
        self._stop_event.set()

    async def list_open_trades(self) -> list[object]:
        return []

    async def close_trade(self, *, trade_id: object, exit_reason: object = None) -> object:
        del trade_id, exit_reason
        raise RuntimeError("not used")


class _Runner:
    def __init__(self, **_: Any) -> None:
        pass

    async def run_forever(self) -> None:
        return None

    def stop(self) -> None:
        return None


def _capture_logs() -> tuple[list[str], int]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{level}|{message}")
    return messages, sink_id


@pytest.mark.asyncio
async def test_create_logged_task_logs_task_failure() -> None:
    messages, sink_id = _capture_logs()

    async def _boom() -> None:
        raise RuntimeError("boom")

    task = create_logged_task(_boom(), name="failing-task")
    await asyncio.gather(task, return_exceptions=True)
    logger.remove(sink_id)

    assert any(
        "ERROR|Background task failed. task_name=failing-task" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_create_logged_task_ignores_cancelled_tasks() -> None:
    messages, sink_id = _capture_logs()

    async def _wait_forever() -> None:
        await asyncio.Event().wait()

    task = create_logged_task(_wait_forever(), name="cancelled-task")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    logger.remove(sink_id)

    assert messages == []


@pytest.mark.asyncio
async def test_create_logged_task_ignores_successful_tasks() -> None:
    messages, sink_id = _capture_logs()

    async def _succeed() -> None:
        return None

    task = create_logged_task(_succeed(), name="successful-task")
    await asyncio.gather(task, return_exceptions=True)
    logger.remove(sink_id)

    assert messages == []


def test_lifespan_uses_logged_task_helper_for_background_runners(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_task_names: list[str] = []
    original_create_logged_task = create_logged_task
    monitoring_position_manager = _MonitoringPositionManager()

    def _recording_create_logged_task(coroutine: Any, *, name: str) -> asyncio.Task[Any]:
        created_task_names.append(name)
        return original_create_logged_task(coroutine, name=name)

    settings.trading_mode = "demo"
    settings.signal_loop_enabled = True
    settings.signal_loop_symbols = ["BTCUSDT"]
    settings.signal_loop_interval = "15"
    settings.scalping_enabled = True
    settings.scalping_symbols = ["ETHUSDT"]
    settings.scalping_interval = "5"
    settings.spot_exit_monitor_interval_seconds = 7.5

    monkeypatch.setattr("backend.main.SignalLoopRunner", _Runner)
    monkeypatch.setattr("backend.main.WebSocketSignalRunner", _Runner)
    monkeypatch.setattr("backend.main.create_logged_task", _recording_create_logged_task)

    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())
    app.state.position_manager = monitoring_position_manager

    with TestClient(app):
        pass

    assert set(created_task_names) >= {
        "signal-loop-runner",
        "scalping-runner",
        "spot-exit-monitor",
    }
    assert monitoring_position_manager.run_calls == [7.5]
    assert monitoring_position_manager.stop_calls == 1
