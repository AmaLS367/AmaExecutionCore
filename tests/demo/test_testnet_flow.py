from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.backtest.demo_runner import DemoRunner
from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.order_executor.executor import OrderExecutor
from backend.position_manager.service import PositionManagerService
from backend.signal_execution.service import ExecutionService
from backend.strategy_engine.contracts import StrategySignal

pytestmark = pytest.mark.testnet


def _should_skip_testnet_flow() -> bool:
    return not (
        os.getenv("AMA_RUN_TESTNET_E2E") == "1"
        and settings.bybit_testnet_api_key
        and settings.bybit_testnet_api_secret
        and settings.demo_testnet_symbol
        and settings.demo_testnet_entry > 0
        and settings.demo_testnet_stop > 0
        and settings.demo_testnet_target > 0
    )


@pytest.mark.asyncio
async def test_demo_runner_executes_real_testnet_cycle(
    testnet_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if _should_skip_testnet_flow():
        pytest.skip("Testnet e2e is disabled or missing required DEMO_TESTNET_* settings.")

    original_mode = settings.trading_mode
    original_order_mode = settings.order_mode
    settings.trading_mode = "demo"
    # Use market orders so the order fills immediately without waiting for maker fill
    settings.order_mode = "taker_allowed"
    try:
        rest_client = BybitRESTClient()
        execution_service = ExecutionService(
            session_factory=testnet_session_factory,
            order_executor=OrderExecutor(rest_client=rest_client),
        )
        position_manager = PositionManagerService(
            session_factory=testnet_session_factory,
            rest_client=rest_client,
        )
        sync_engine = ExchangeSyncEngine(
            testnet_session_factory,
            rest_client=rest_client,
            reconciliation_interval_seconds=1.0,
        )
        runner = DemoRunner(
            execution_service=execution_service,
            position_manager=position_manager,
            session_factory=testnet_session_factory,
            sync_engine=sync_engine,
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
        settings.order_mode = original_order_mode
