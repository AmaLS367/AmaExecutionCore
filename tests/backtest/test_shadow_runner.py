from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.backtest.shadow_runner import ShadowRunner
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


@dataclass
class FakeMarketSnapshot:
    symbol: str
    last_price: float


class FakeSnapshotProvider:
    async def get_snapshot(self, symbol: str) -> FakeMarketSnapshot:
        return FakeMarketSnapshot(symbol=symbol, last_price=100.0)


class FixedStrategy(BaseStrategy[FakeMarketSnapshot]):
    async def generate_signal(self, snapshot: FakeMarketSnapshot) -> StrategySignal | None:
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=100.0,
            stop=90.0,
            target=130.0,
            reason="runner-test",
        )


class PassiveExecutionService:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_signal(self, *, signal: StrategySignal) -> StrategySignal:
        self.calls += 1
        return signal


@pytest.mark.asyncio
async def test_shadow_runner_executes_generated_signal() -> None:
    runner = ShadowRunner(
        snapshot_provider=FakeSnapshotProvider(),
        strategy=FixedStrategy(),
        execution_service=PassiveExecutionService(),
    )

    result = await runner.run_once("BTCUSDT")

    assert result is not None
    assert result.symbol == "BTCUSDT"
