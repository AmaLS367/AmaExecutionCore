from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.market_data.contracts import (
    MarketCandle,
    MarketSnapshot,
    MarketSnapshotProvider,
    MarketSnapshotRequest,
)
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionService


class RecordingProvider(MarketSnapshotProvider[MarketSnapshot]):
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[MarketSnapshotRequest] = []

    async def get_snapshot(self, request: MarketSnapshotRequest) -> MarketSnapshot:
        self.requests.append(request)
        return self.snapshot


class FixedStrategy:
    required_candle_count = 22

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 10,
            target=snapshot.last_price + 20,
            reason="ema",
        )


@pytest.mark.asyncio
async def test_strategy_execution_service_requests_snapshot_for_symbol_and_interval() -> None:
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        interval="15",
        candles=(
            MarketCandle(
                opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                high=110.0,
                low=95.0,
                close=101.0,
            ),
        ),
    )
    provider = RecordingProvider(snapshot=snapshot)
    service = StrategyExecutionService(snapshot_provider=provider, strategy=FixedStrategy())

    result = await service.run(StrategyExecutionRequest(symbol="BTCUSDT", interval="15"))

    assert provider.requests == [MarketSnapshotRequest(symbol="BTCUSDT", interval="15", limit=22)]
    assert result.snapshot is snapshot
    assert result.signal is not None
    assert result.signal.entry == snapshot.last_price
