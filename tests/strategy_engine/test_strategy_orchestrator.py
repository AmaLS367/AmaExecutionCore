from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.market_data.contracts import (
    MarketCandle,
    MarketSnapshot,
    MarketSnapshotProvider,
    MarketSnapshotRequest,
)
from backend.strategy_engine import StrategyOrchestrator
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionService


class RecordingSnapshotProvider(MarketSnapshotProvider[MarketSnapshot]):
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[MarketSnapshotRequest] = []

    async def get_snapshot(self, request: MarketSnapshotRequest) -> MarketSnapshot:
        self.requests.append(request)
        return self.snapshot


class PassiveStrategy:
    def __init__(self, *, required_candle_count: int) -> None:
        self.required_candle_count = required_candle_count
        self.calls = 0

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self.calls += 1
        return None


class FixedSignalStrategy:
    def __init__(self, *, required_candle_count: int, reason: str) -> None:
        self.required_candle_count = required_candle_count
        self.reason = reason
        self.calls = 0

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self.calls += 1
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 5.0,
            target=snapshot.last_price + 10.0,
            reason=self.reason,
            strategy_version=f"{self.reason}-v1",
        )


@pytest.mark.asyncio
async def test_strategy_orchestrator_short_circuits_to_first_signal_in_order() -> None:
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        interval="1",
        candles=(
            MarketCandle(
                opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                high=110.0,
                low=90.0,
                close=100.0,
            ),
        ),
    )
    first = PassiveStrategy(required_candle_count=5)
    second = FixedSignalStrategy(required_candle_count=8, reason="breakout")
    third = FixedSignalStrategy(required_candle_count=13, reason="mean-reversion")
    orchestrator: StrategyOrchestrator[MarketSnapshot] = StrategyOrchestrator(
        strategies=(first, second, third)
    )

    signal = await orchestrator.generate_signal(snapshot)

    assert orchestrator.required_candle_count == 13
    assert signal is not None
    assert signal.reason == "breakout"
    assert first.calls == 1
    assert second.calls == 1
    assert third.calls == 0


@pytest.mark.asyncio
async def test_strategy_execution_service_uses_orchestrator_highest_candle_requirement() -> None:
    snapshot = MarketSnapshot(
        symbol="ETHUSDT",
        interval="15",
        candles=(
            MarketCandle(
                opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                high=210.0,
                low=190.0,
                close=201.0,
            ),
        ),
    )
    provider = RecordingSnapshotProvider(snapshot)
    orchestrator: StrategyOrchestrator[MarketSnapshot] = StrategyOrchestrator(
        strategies=(
            PassiveStrategy(required_candle_count=9),
            FixedSignalStrategy(required_candle_count=21, reason="ema"),
        )
    )
    service = StrategyExecutionService(snapshot_provider=provider, strategy=orchestrator)

    result = await service.run(StrategyExecutionRequest(symbol=" ethusdt ", interval=" 15 "))

    assert provider.requests == [MarketSnapshotRequest(symbol="ETHUSDT", interval="15", limit=21)]
    assert result.signal is not None
    assert result.signal.reason == "ema"
