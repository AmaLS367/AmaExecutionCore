from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.backtest.shadow_runner import ShadowRunRequest, ShadowRunner
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionResult


class FixedStrategyExecutionService:
    async def run(
        self,
        request: StrategyExecutionRequest,
    ) -> StrategyExecutionResult[MarketSnapshot]:
        snapshot = MarketSnapshot(
            symbol=request.symbol,
            interval=request.interval,
            candles=(
                MarketCandle(
                    opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                    high=110.0,
                    low=90.0,
                    close=100.0,
                ),
            ),
        )
        signal = StrategySignal(
            symbol=request.symbol,
            direction="long",
            entry=100.0,
            stop=90.0,
            target=130.0,
            reason="runner-test",
        )
        return StrategyExecutionResult(request=request, snapshot=snapshot, signal=signal)


class PassiveExecutionService:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> ExecuteSignalRequest:
        self.calls += 1
        return signal


@pytest.mark.asyncio
async def test_shadow_runner_executes_generated_signal() -> None:
    execution_service = PassiveExecutionService()
    runner = ShadowRunner(
        strategy_execution_service=FixedStrategyExecutionService(),
        execution_service=execution_service,
    )

    result = await runner.run_once(ShadowRunRequest(symbol="BTCUSDT", interval="1"))

    assert result.execution is not None
    assert result.execution.symbol == "BTCUSDT"
    assert execution_service.calls == 1
