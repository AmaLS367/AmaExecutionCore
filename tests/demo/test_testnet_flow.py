from __future__ import annotations

import os
from typing import Protocol

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

_MANAGED_DEMO_ORDER_PREFIXES = ("tp_", "stop_", "close_")


class _ManagedOrderCleanupClient(Protocol):
    def get_open_orders(self, *, category: str, symbol: str) -> list[dict[str, object]]: ...

    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_link_id: str,
    ) -> dict[str, object]: ...


class _FakeCleanupClient:
    def __init__(self) -> None:
        self.cancelled_order_link_ids: list[str] = []

    def get_open_orders(self, *, category: str, symbol: str) -> list[dict[str, object]]:
        return [
            {"orderLinkId": "tp_test", "orderId": "1"},
            {"orderLinkId": "stop_test", "orderId": "2"},
            {"orderLinkId": "manual_order", "orderId": "3"},
        ]

    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_link_id: str,
    ) -> dict[str, object]:
        self.cancelled_order_link_ids.append(order_link_id)
        return {}


def _cancel_managed_demo_orders(
    client: _ManagedOrderCleanupClient,
    *,
    symbol: str,
) -> int:
    cancelled = 0
    for order in client.get_open_orders(category="spot", symbol=symbol):
        order_link_id = order.get("orderLinkId")
        if not isinstance(order_link_id, str):
            continue
        if not order_link_id.startswith(_MANAGED_DEMO_ORDER_PREFIXES):
            continue
        client.cancel_order(category="spot", symbol=symbol, order_link_id=order_link_id)
        cancelled += 1
    return cancelled


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


def test_cleanup_cancels_only_managed_demo_orders() -> None:
    client = _FakeCleanupClient()

    cancelled = _cancel_managed_demo_orders(client, symbol="BTCUSDT")

    assert cancelled == 2
    assert client.cancelled_order_link_ids == ["tp_test", "stop_test"]


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
    rest_client: BybitRESTClient | None = None
    try:
        rest_client = BybitRESTClient()
        _cancel_managed_demo_orders(rest_client, symbol=settings.demo_testnet_symbol)
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
            ),
        )
        assert trade.status.value in {"pnl_recorded", "position_close_failed"}
    finally:
        if rest_client is not None:
            _cancel_managed_demo_orders(rest_client, symbol=settings.demo_testnet_symbol)
        settings.trading_mode = original_mode
        settings.order_mode = original_order_mode
