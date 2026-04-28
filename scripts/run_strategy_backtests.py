from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import sin
from pathlib import Path
from typing import Literal, cast

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from loguru import logger

from backend.backtest import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.backtest.datasets import (
    SupportsKlineFetch,
    candles_for_lookback,
    fetch_candles_with_retry,
)
from backend.backtest.replay_runner import (
    SupportsReplayExecutionContext,
    SupportsReplayStrategy,
)
from backend.bybit_client.rest import (
    BybitKline,
    BybitRESTClient,
)
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.factory import (
    build_day_trading_strategy,
    build_scalping_strategy,
)

StrategyFamily = Literal["day_trading", "scalping"]


@dataclass(slots=True, frozen=True)
class BacktestCase:
    family: StrategyFamily
    strategy_name: str
    interval: str
    lookback_days: int
    min_rrr: float
    max_hold_candles: int = 20


@dataclass(slots=True, frozen=True)
class BacktestSummary:
    trades: int
    win_rate: Decimal | None
    profit_factor: Decimal | None


class SyntheticKlineClient:
    def __init__(self, *, symbol: str) -> None:
        self._symbol = symbol
        self._datasets = {
            interval: self._build_dataset(interval=interval)
            for interval in sorted({case.interval for case in BACKTEST_CASES}, key=int)
        }

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str,
        end: int | None = None,
    ) -> list[BybitKline]:
        del category
        if symbol != self._symbol:
            return []
        candles = self._datasets.get(interval, ())
        eligible = candles
        if end is not None:
            eligible = tuple(
                candle
                for candle in candles
                if int(candle.start_time.timestamp() * 1000) <= end
            )
        if not eligible:
            return []
        return list(reversed(eligible[-limit:]))

    def _build_dataset(self, *, interval: str) -> tuple[BybitKline, ...]:
        total = candles_for_lookback(interval=interval, lookback_days=90)
        minutes = int(interval)
        opened_at = datetime.now(tz=UTC) - timedelta(minutes=minutes * (total - 1))
        close = 100.0
        candles: list[BybitKline] = []
        for index in range(total):
            trend_leg = (index // 240) % 4
            if trend_leg == 0:
                drift = 0.22
            elif trend_leg == 1:
                drift = -0.18
            elif trend_leg == 2:
                drift = 0.08
            else:
                drift = -0.06
            oscillation = sin(index / 6) * 1.8 + sin(index / 29) * 3.2
            close = max(10.0, close + drift + oscillation * 0.08)
            high = close + 1.2 + abs(sin(index / 4)) * 0.6
            low = close - 1.2 - abs(sin(index / 5)) * 0.6
            volume = 1000.0 + abs(sin(index / 7)) * 250.0
            candles.append(
                BybitKline(
                    start_time=opened_at + timedelta(minutes=minutes * index),
                    open_price=close,
                    high_price=high,
                    low_price=low,
                    close_price=close,
                    volume=volume,
                    turnover=close * volume,
                ),
            )
        return tuple(candles)


BACKTEST_CASES: tuple[BacktestCase, ...] = (
    BacktestCase(
        family="scalping",
        strategy_name="vwap_reversion",
        interval="5",
        lookback_days=90,
        min_rrr=1.5,
    ),
    BacktestCase(
        family="day_trading",
        strategy_name="ema_crossover",
        interval="15",
        lookback_days=90,
        min_rrr=2.0,
    ),
    BacktestCase(
        family="day_trading",
        strategy_name="ema_crossover",
        interval="60",
        lookback_days=90,
        min_rrr=2.0,
    ),
    BacktestCase(
        family="day_trading",
        strategy_name="ema_crossover",
        interval="15",
        lookback_days=90,
        min_rrr=2.0,
    ),
)


def _build_parser() -> argparse.ArgumentParser:
    logger.remove()
    parser = argparse.ArgumentParser(description="Run the BTCUSDT strategy backtest matrix.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp_outputs/strategy-backtests-btcusdt-90d.json"),
    )
    parser.add_argument("--fee-rate-per-side", type=float, default=0.001)
    parser.add_argument(
        "--mock-data",
        action="store_true",
        help="Use synthetic candles instead of Bybit REST. Useful when outbound network is blocked.",
    )
    return parser


def _build_strategy(case: BacktestCase) -> object:
    if case.family == "day_trading":
        return build_day_trading_strategy(
            strategy_name=case.strategy_name,
            min_rrr=case.min_rrr,
        )
    return build_scalping_strategy(
        strategy_name=case.strategy_name,
        min_rrr=case.min_rrr,
    )


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "None"
    return str(value)


def _summarize_executions(
    executions: tuple[SimulationExecutionResult, ...],
) -> BacktestSummary:
    net_trade_pnls = tuple(
        execution.realized_pnl - execution.fees_paid
        for execution in executions
    )
    trades = len(net_trade_pnls)
    wins = sum(1 for pnl in net_trade_pnls if pnl > 0)
    gross_wins = sum((pnl for pnl in net_trade_pnls if pnl > 0), Decimal(0))
    gross_losses = sum((abs(pnl) for pnl in net_trade_pnls if pnl < 0), Decimal(0))

    win_rate = Decimal(wins) / Decimal(trades) if trades else None
    if gross_losses == 0:
        profit_factor = None if gross_wins == 0 else Decimal("Infinity")
    else:
        profit_factor = gross_wins / gross_losses

    return BacktestSummary(
        trades=trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
    )


async def run_backtests(
    *,
    symbol: str,
    output_path: Path,
    fee_rate_per_side: float,
    client: SupportsKlineFetch | None = None,
) -> list[dict[str, object]]:
    rest_client = client or BybitRESTClient()
    candles_by_interval: dict[str, tuple[MarketCandle, ...]] = {}
    for interval in sorted({case.interval for case in BACKTEST_CASES}, key=int):
        lookback_days = max(
            case.lookback_days
            for case in BACKTEST_CASES
            if case.interval == interval
        )
        candles = await fetch_candles_with_retry(
            rest_client,
            symbol=symbol,
            interval=interval,
            lookback_days=lookback_days,
        )
        candles_by_interval[interval] = candles

    results: list[dict[str, object]] = []
    for case in BACKTEST_CASES:
        strategy = _build_strategy(case)
        simulation_service = SimulationExecutionService(
            max_hold_candles=case.max_hold_candles,
            risk_amount_usd=100.0,
            fee_rate_per_side=fee_rate_per_side,
            market_mode="spot",
        )
        runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
            strategy=cast("SupportsReplayStrategy", strategy),
            execution_service=cast(
                "SupportsReplayExecutionContext[SimulationExecutionResult]",
                simulation_service,
            ),
        )
        replay_result = await runner.replay(
            HistoricalReplayRequest(
                symbol=symbol,
                interval=case.interval,
                candles=candles_by_interval[case.interval],
            ),
        )
        executions = tuple(
            step.execution
            for step in replay_result.steps
            if step.execution is not None
        )
        summary = _summarize_executions(executions)
        serialized = {
            "family": case.family,
            "strategy_name": case.strategy_name,
            "interval": case.interval,
            "lookback_days": case.lookback_days,
            "trades": summary.trades,
            "win_rate": _format_decimal(summary.win_rate),
            "profit_factor": _format_decimal(summary.profit_factor),
        }
        results.append(serialized)
        print(
            f"{case.strategy_name} family={case.family} interval={case.interval} "
            f"trades={serialized['trades']} win_rate={serialized['win_rate']} "
            f"profit_factor={serialized['profit_factor']}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


async def main() -> None:
    args = _build_parser().parse_args()
    symbol = str(args.symbol).strip().upper()
    client: SupportsKlineFetch | None = None
    if args.mock_data:
        client = SyntheticKlineClient(symbol=symbol)
    await run_backtests(
        symbol=symbol,
        output_path=args.output,
        fee_rate_per_side=args.fee_rate_per_side,
        client=client,
    )


if __name__ == "__main__":
    asyncio.run(main())
