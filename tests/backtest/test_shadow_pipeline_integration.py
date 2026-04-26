from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.backtest.shadow_runner import ShadowRunner, ShadowRunRequest
from backend.config import settings
from backend.market_data.contracts import (
    MarketCandle,
    MarketSnapshot,
    MarketSnapshotProvider,
    MarketSnapshotRequest,
)
from backend.order_executor.executor import OrderExecutor
from backend.signal_execution.service import ExecutionResult, ExecutionService
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.service import StrategyExecutionService
from backend.trade_journal.models import Signal, Trade, TradeStatus


class FakeSnapshotProvider(MarketSnapshotProvider[MarketSnapshot]):
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[MarketSnapshotRequest] = []

    async def get_snapshot(self, request: MarketSnapshotRequest) -> MarketSnapshot:
        self.requests.append(request)
        return self.snapshot


class NoExchangeRestClient:
    def __init__(self) -> None:
        self.wallet_calls = 0
        self.instrument_calls = 0
        self.place_order_calls = 0

    def get_wallet_balance(self) -> dict[str, object]:
        self.wallet_calls += 1
        raise AssertionError("Shadow flow must not fetch exchange wallet balance.")

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        self.instrument_calls += 1
        raise AssertionError("Shadow flow must not fetch exchange instrument metadata.")

    def place_order(self, **_: object) -> dict[str, object]:
        self.place_order_calls += 1
        raise AssertionError("Shadow flow must not place exchange orders.")


def build_snapshot(closes: list[float], *, last_high: float, last_low: float) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=close + 1 if index < len(closes) - 1 else last_high,
            low=close - 1 if index < len(closes) - 1 else last_low,
            close=close,
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="1", candles=candles)


def build_shadow_runner(
    *,
    snapshot: MarketSnapshot,
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    rest_client: NoExchangeRestClient,
) -> tuple[ShadowRunner[ExecutionResult], FakeSnapshotProvider]:
    provider = FakeSnapshotProvider(snapshot=snapshot)
    strategy_service = StrategyExecutionService(
        snapshot_provider=provider,
        strategy=EMACrossoverStrategy(),
    )
    execution_service = ExecutionService(
        session_factory=sqlite_session_factory,
        order_executor=OrderExecutor(rest_client=rest_client),
    )
    return (
        ShadowRunner(
            strategy_execution_service=strategy_service,
            execution_service=execution_service,
        ),
        provider,
    )


@pytest.mark.asyncio
async def test_shadow_runner_persists_execution_from_snapshot_signal_flow(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    snapshot = build_snapshot([100.0] * 21 + [130.0], last_high=132.0, last_low=120.0)
    rest_client = NoExchangeRestClient()
    runner, provider = build_shadow_runner(
        snapshot=snapshot,
        sqlite_session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    result = await runner.run_once(ShadowRunRequest(symbol=" btcusdt ", interval=" 1 "))

    assert provider.requests == [MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=22)]
    assert result.signal is not None
    assert result.execution is not None
    assert result.execution.status == TradeStatus.ORDER_SUBMITTED.value
    assert rest_client.place_order_calls == 0

    async with sqlite_session_factory() as session:
        signal = (await session.execute(select(Signal))).scalar_one()
        trade = (await session.execute(select(Trade))).scalar_one()

    assert signal.symbol == "BTCUSDT"
    assert signal.reason == "ema_9_21_crossover"
    assert trade.signal_id == signal.id
    assert trade.status == TradeStatus.ORDER_SUBMITTED
    assert trade.order_link_id is not None


@pytest.mark.asyncio
async def test_shadow_runner_skips_execution_when_strategy_returns_no_signal(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    snapshot = build_snapshot([100.0] * 22, last_high=101.0, last_low=99.0)
    rest_client = NoExchangeRestClient()
    runner, provider = build_shadow_runner(
        snapshot=snapshot,
        sqlite_session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    result = await runner.run_once(ShadowRunRequest(symbol="BTCUSDT", interval="1"))

    assert provider.requests == [MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=22)]
    assert result.signal is None
    assert result.execution is None
    assert rest_client.place_order_calls == 0

    async with sqlite_session_factory() as session:
        signal_count = (await session.execute(select(func.count(Signal.id)))).scalar_one()
        trade_count = (await session.execute(select(func.count(Trade.id)))).scalar_one()

    assert signal_count == 0
    assert trade_count == 0
