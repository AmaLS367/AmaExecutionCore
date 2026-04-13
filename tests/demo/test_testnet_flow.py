from __future__ import annotations

import os

import pytest

from backend.backtest.demo_runner import DemoRunner
from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.order_executor.executor import OrderExecutor
from backend.position_manager.service import PositionManagerService
from backend.signal_execution.service import ExecutionService
from backend.strategy_engine.contracts import StrategySignal

pytestmark = pytest.mark.testnet


def _should_skip_testnet_flow() -> bool:
    return not (
        os.getenv("AMA_RUN_TESTNET_E2E") == "1"
        and settings.bybit_api_key
        and settings.bybit_api_secret
        and settings.database_url
        and settings.demo_testnet_symbol
        and settings.demo_testnet_entry > 0
        and settings.demo_testnet_stop > 0
        and settings.demo_testnet_target > 0
    )


@pytest.mark.asyncio
async def test_demo_runner_executes_real_testnet_cycle() -> None:
    if _should_skip_testnet_flow():
        pytest.skip("Testnet e2e is disabled or missing required DEMO_TESTNET_* settings.")

    original_mode = settings.trading_mode
    settings.trading_mode = "demo"
    try:
        rest_client = BybitRESTClient()
        execution_service = ExecutionService(
            session_factory=AsyncSessionLocal,
            order_executor=OrderExecutor(rest_client=rest_client),
        )
        position_manager = PositionManagerService(
            session_factory=AsyncSessionLocal,
            rest_client=rest_client,
        )
        runner = DemoRunner(
            execution_service=execution_service,
            position_manager=position_manager,
            session_factory=AsyncSessionLocal,
        )
        trade = await runner.execute_and_close(
            signal=StrategySignal(
                symbol=settings.demo_testnet_symbol,
                direction="long",
                entry=settings.demo_testnet_entry,
                stop=settings.demo_testnet_stop,
                target=settings.demo_testnet_target,
                reason="pytest-testnet-e2e",
                strategy_version="testnet-e2e",
            )
        )
        assert trade.status.value in {"pnl_recorded", "position_close_failed"}
    finally:
        settings.trading_mode = original_mode
