"""Bounded sweep for RSI EMA spot-v2 candidates on 365d 15m fixtures."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from statistics import median
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner
from backend.backtest.datasets import load_dataset
from backend.backtest.replay_runner import SupportsReplayExecutionContext, SupportsReplayStrategy
from backend.backtest.simulation_execution_service import (
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.market_data.intervals import interval_to_minutes
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.htf_trend_filter import (
    aggregate_complete_candles,
    is_bullish_htf_trend,
)
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy

FIXTURES: dict[str, Path] = {
    "BTCUSDT": Path("scripts/fixtures/regression/btcusdt_15m_365d.json.gz"),
    "ETHUSDT": Path("scripts/fixtures/regression/ethusdt_15m_365d.json.gz"),
    "SOLUSDT": Path("scripts/fixtures/regression/solusdt_15m_365d.json.gz"),
}

LTF_EMA_PERIODS = (20, 50)
RSI_THRESHOLDS = ((40.0, 60.0), (38.0, 62.0), (35.0, 65.0))
HTF_EMA_PERIODS = (20, 50)
HTF_REQUIRE_SLOPE = (False, True)
MAX_HOLD_CANDLES = (20, 32)
MIN_RRR = 1.5
RISK_AMOUNT_USD = 100.0
FEE_RATE_PER_SIDE = 0.001
MIN_CLOSED_TRADES = 15
MIN_WIN_RATE = 0.51
MIN_PROFIT_FACTOR = 1.1


@dataclass(slots=True, frozen=True)
class SweepParams:
    ltf_ema_period: int
    rsi_oversold: float
    rsi_overbought: float
    htf_ema_period: int
    htf_require_slope: bool
    max_hold_candles: int


@dataclass(slots=True, frozen=True)
class SymbolResult:
    symbol: str
    closed_trades: int
    winning_trades: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    net_pnl: Decimal

    @property
    def passed(self) -> bool:
        return (
            self.closed_trades >= MIN_CLOSED_TRADES
            and self.win_rate is not None
            and self.win_rate >= Decimal(str(MIN_WIN_RATE))
            and self.profit_factor is not None
            and self.profit_factor >= Decimal(str(MIN_PROFIT_FACTOR))
            and self.expectancy is not None
            and self.expectancy > 0
        )


@dataclass(slots=True, frozen=True)
class SweepResult:
    params: SweepParams
    symbols: tuple[SymbolResult, ...]

    @property
    def passed(self) -> bool:
        return all(symbol.passed for symbol in self.symbols)

    @property
    def median_profit_factor(self) -> float:
        values = [
            float(symbol.profit_factor)
            for symbol in self.symbols
            if symbol.profit_factor is not None
        ]
        return median(values) if values else 0.0

    @property
    def total_closed_trades(self) -> int:
        return sum(symbol.closed_trades for symbol in self.symbols)


class PrecomputedHTFSpotV2Strategy:
    def __init__(self, *, params: SweepParams, htf_pass_by_time: dict[datetime, bool]) -> None:
        self._inner = RSIEMAStrategy(
            ema_period=params.ltf_ema_period,
            rsi_period=14,
            rsi_oversold=params.rsi_oversold,
            rsi_overbought=params.rsi_overbought,
            min_rrr=MIN_RRR,
            target_rrr=MIN_RRR,
        )
        self._htf_pass_by_time = htf_pass_by_time

    @property
    def required_candle_count(self) -> int:
        return self._inner.required_candle_count

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        if not self._htf_pass_by_time.get(snapshot.candles[-1].opened_at, False):
            return None
        signal = await self._inner.generate_signal(snapshot)
        if signal is None or signal.direction == "short":
            return None
        signal.strategy_version = "rsi-ema-spot-v2"
        return signal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep bounded RSI EMA spot-v2 candidates.")
    parser.add_argument("--quiet", action="store_true")
    return parser


async def _evaluate_symbol(
    *,
    params: SweepParams,
    symbol: str,
    candles: tuple[MarketCandle, ...],
    htf_pass_by_time: dict[datetime, bool],
) -> SymbolResult:
    strategy = PrecomputedHTFSpotV2Strategy(
        params=params,
        htf_pass_by_time=htf_pass_by_time,
    )
    execution_service = SimulationExecutionService(
        max_hold_candles=params.max_hold_candles,
        risk_amount_usd=RISK_AMOUNT_USD,
        fee_rate_per_side=FEE_RATE_PER_SIDE,
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=cast("SupportsReplayStrategy", strategy),
        execution_service=cast(
            "SupportsReplayExecutionContext[SimulationExecutionResult]",
            execution_service,
        ),
    )
    result = await runner.replay(
        HistoricalReplayRequest(symbol=symbol, interval="15", candles=candles),
    )
    net_trade_pnls = tuple(
        step.execution.realized_pnl - step.execution.fees_paid
        for step in result.steps
        if step.execution is not None
    )
    closed_trades = len(net_trade_pnls)
    winning_trades = sum(1 for pnl in net_trade_pnls if pnl > 0)
    gross_wins = sum((pnl for pnl in net_trade_pnls if pnl > 0), Decimal(0))
    gross_losses = sum((abs(pnl) for pnl in net_trade_pnls if pnl < 0), Decimal(0))
    net_pnl = sum(net_trade_pnls, Decimal(0))
    win_rate = Decimal(winning_trades) / Decimal(closed_trades) if closed_trades else None
    expectancy = net_pnl / Decimal(closed_trades) if closed_trades else None
    if gross_losses == 0:
        profit_factor = None if gross_wins == 0 else Decimal("Infinity")
    else:
        profit_factor = gross_wins / gross_losses
    return SymbolResult(
        symbol=symbol,
        closed_trades=closed_trades,
        winning_trades=winning_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        net_pnl=net_pnl,
    )


async def _evaluate_params(
    *,
    params: SweepParams,
    datasets: dict[str, tuple[MarketCandle, ...]],
    htf_cache: dict[tuple[str, int, bool], dict[datetime, bool]],
) -> SweepResult:
    return SweepResult(
        params=params,
        symbols=tuple(
            [
                await _evaluate_symbol(
                    params=params,
                    symbol=symbol,
                    candles=candles,
                    htf_pass_by_time=htf_cache[
                        (symbol, params.htf_ema_period, params.htf_require_slope)
                    ],
                )
                for symbol, candles in datasets.items()
            ],
        ),
    )


def _build_htf_pass_by_time(
    *,
    candles: tuple[MarketCandle, ...],
    htf_ema_period: int,
    htf_require_slope: bool,
) -> dict[datetime, bool]:
    source_minutes = interval_to_minutes("15")
    target_minutes = interval_to_minutes("240")
    candles_per_bucket = target_minutes // source_minutes
    htf_candles = aggregate_complete_candles(
        candles,
        source_interval="15",
        target_interval="240",
    )
    htf_pass = [
        (
            is_bullish_htf_trend(
                htf_candles[: index + 1],
                ema_period=htf_ema_period,
                slope_lookback=5,
                require_slope=htf_require_slope,
            )
        )
        for index in range(len(htf_candles))
    ]

    pass_by_time: dict[datetime, bool] = {}
    htf_index = -1
    for candle in candles:
        while htf_index + 1 < len(htf_candles):
            next_htf = htf_candles[htf_index + 1]
            next_complete_at = next_htf.opened_at + timedelta(
                minutes=source_minutes * (candles_per_bucket - 1),
            )
            if next_complete_at > candle.opened_at:
                break
            htf_index += 1
        pass_by_time[candle.opened_at] = htf_index >= 0 and htf_pass[htf_index]
    return pass_by_time


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "None"
    return f"{float(value):.3f}"


def _format_params(params: SweepParams) -> str:
    htf_mode = "slope5" if params.htf_require_slope else "above_ema"
    return (
        f"ltf_ema={params.ltf_ema_period} "
        f"rsi={params.rsi_oversold:.0f}/{params.rsi_overbought:.0f} "
        f"htf_ema={params.htf_ema_period} htf_mode={htf_mode} "
        f"hold={params.max_hold_candles}"
    )


def _print_result(result: SweepResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(
        f"[{status}] {_format_params(result.params)} "
        f"total_trades={result.total_closed_trades} "
        f"median_pf={result.median_profit_factor:.3f}",
    )
    for symbol_result in result.symbols:
        print(
            f"  {symbol_result.symbol}: trades={symbol_result.closed_trades} "
            f"wr={_format_decimal(symbol_result.win_rate)} "
            f"pf={_format_decimal(symbol_result.profit_factor)} "
            f"expectancy={_format_decimal(symbol_result.expectancy)} "
            f"net_pnl={float(symbol_result.net_pnl):.2f}",
        )


async def main() -> None:
    args = _build_parser().parse_args()
    datasets = {symbol: load_dataset(path).candles for symbol, path in FIXTURES.items()}
    htf_cache = {
        (symbol, htf_ema_period, htf_require_slope): _build_htf_pass_by_time(
            candles=candles,
            htf_ema_period=htf_ema_period,
            htf_require_slope=htf_require_slope,
        )
        for symbol, candles in datasets.items()
        for htf_ema_period in HTF_EMA_PERIODS
        for htf_require_slope in HTF_REQUIRE_SLOPE
    }

    base_params = [
        SweepParams(
            ltf_ema_period=ltf_ema_period,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            htf_ema_period=htf_ema_period,
            htf_require_slope=htf_require_slope,
            max_hold_candles=20,
        )
        for (
            ltf_ema_period,
            (rsi_oversold, rsi_overbought),
            htf_ema_period,
            htf_require_slope,
        ) in product(
            LTF_EMA_PERIODS,
            RSI_THRESHOLDS,
            HTF_EMA_PERIODS,
            HTF_REQUIRE_SLOPE,
        )
    ]

    results: list[SweepResult] = []
    for params in base_params:
        result = await _evaluate_params(params=params, datasets=datasets, htf_cache=htf_cache)
        results.append(result)
        if not args.quiet:
            _print_result(result)

    hold_32_candidates = [
        result.params
        for result in results
        if all(symbol.closed_trades >= MIN_CLOSED_TRADES for symbol in result.symbols)
    ]
    for params in hold_32_candidates:
        hold_32_params = SweepParams(
            ltf_ema_period=params.ltf_ema_period,
            rsi_oversold=params.rsi_oversold,
            rsi_overbought=params.rsi_overbought,
            htf_ema_period=params.htf_ema_period,
            htf_require_slope=params.htf_require_slope,
            max_hold_candles=32,
        )
        result = await _evaluate_params(
            params=hold_32_params,
            datasets=datasets,
            htf_cache=htf_cache,
        )
        results.append(result)
        if not args.quiet:
            _print_result(result)

    passing = [result for result in results if result.passed]
    passing.sort(
        key=lambda result: (result.median_profit_factor, result.total_closed_trades),
        reverse=True,
    )
    print("\n=== RSI EMA spot-v2 sweep result ===")
    if not passing:
        print("No candidate passed all per-symbol thresholds.")
        best = sorted(
            results,
            key=lambda result: (result.median_profit_factor, result.total_closed_trades),
            reverse=True,
        )[:5]
        print("\nTop 5 by median profit factor:")
        for result in best:
            _print_result(result)
        raise SystemExit(1)

    print("Selected candidate:")
    _print_result(passing[0])


if __name__ == "__main__":
    asyncio.run(main())
