from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from loguru import logger

from backend.backtest import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.backtest.datasets import candles_for_lookback, fetch_candles, load_dataset
from backend.backtest.replay_runner import SupportsReplayExecutionContext, SupportsReplayStrategy
from backend.bybit_client.rest import BybitRESTClient
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.factory import build_day_trading_strategy, build_scalping_strategy


@dataclass(slots=True, frozen=True)
class FixtureCandle:
    opened_at: str
    high: float
    low: float
    close: float
    volume: float = 0.0
    open: float | None = None


@dataclass(slots=True, frozen=True)
class FixturePayload:
    symbol: str
    interval: str
    candles: tuple[FixtureCandle, ...]
    lookback_days: int | None = None


@dataclass(slots=True, frozen=True)
class BacktestThresholds:
    min_closed_trades: int | None = None
    min_win_rate: float | None = None
    min_profit_factor: float | None = None
    max_drawdown: float | None = None


class BacktestExpectationError(RuntimeError):
    """Raised when single-run backtest metrics do not satisfy CLI thresholds."""


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a historical strategy backtest.")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--symbol")
    parser.add_argument("--interval")
    parser.add_argument("--candles", type=int)
    parser.add_argument("--lookback-days", type=int)
    parser.add_argument("--family", default="day_trading", choices=("day_trading", "scalping"))
    parser.add_argument("--strategy", default="ema_crossover")
    parser.add_argument("--min-rrr", type=float, default=1.5)
    parser.add_argument("--risk-amount", type=float, default=100.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--fee-rate-per-side", type=float, default=0.001)
    parser.add_argument("--min-closed-trades", type=int)
    parser.add_argument("--min-win-rate", type=float)
    parser.add_argument("--min-profit-factor", type=float)
    parser.add_argument("--max-drawdown", type=float)
    parser.add_argument("--max-drawdown-pct", type=float)
    return parser


def _load_candles(*, args: argparse.Namespace) -> tuple[str, str, int | None, tuple[MarketCandle, ...]]:
    if args.fixture is not None:
        try:
            dataset = load_dataset(args.fixture)
        except KeyError:
            payload = _load_fixture_payload(args.fixture)
            return (
                args.symbol or payload.symbol,
                args.interval or payload.interval,
                args.lookback_days or payload.lookback_days,
                _fixture_payload_to_market_candles(payload),
            )
        return (
            args.symbol or dataset.symbol,
            args.interval or dataset.interval,
            dataset.lookback_days,
            dataset.candles,
        )

    symbol = (args.symbol or "BTCUSDT").strip().upper()
    interval = (args.interval or "15").strip()
    total_candles = args.candles
    lookback_days = args.lookback_days
    if total_candles is None:
        total_candles = candles_for_lookback(interval=interval, lookback_days=lookback_days or 30)

    client = BybitRESTClient()
    candles = fetch_candles(
        client,
        symbol=symbol,
        interval=interval,
        total=total_candles,
    )
    return symbol, interval, lookback_days, candles


def _load_fixture_payload(path: Path) -> FixturePayload:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise TypeError("Fixture payload must be a JSON object.")

    symbol = str(raw_payload.get("symbol", "")).strip().upper()
    interval = str(raw_payload.get("interval", "")).strip()
    raw_candles = raw_payload.get("candles")
    raw_lookback_days = raw_payload.get("lookback_days")
    if not symbol:
        raise ValueError("Fixture payload must define a non-empty symbol.")
    if not interval:
        raise ValueError("Fixture payload must define a non-empty interval.")
    if not isinstance(raw_candles, list) or not raw_candles:
        raise ValueError("Fixture payload must define a non-empty candles list.")

    candles = tuple(
        FixtureCandle(
            opened_at=str(raw_candle["opened_at"]),
            high=float(raw_candle["high"]),
            low=float(raw_candle["low"]),
            close=float(raw_candle["close"]),
            volume=float(raw_candle.get("volume", 0.0)),
            open=float(raw_candle["open"]) if "open" in raw_candle else None,
        )
        for raw_candle in raw_candles
        if isinstance(raw_candle, dict)
    )
    lookback_days = int(raw_lookback_days) if raw_lookback_days is not None else None
    return FixturePayload(
        symbol=symbol,
        interval=interval,
        candles=candles,
        lookback_days=lookback_days,
    )


def _fixture_payload_to_market_candles(payload: FixturePayload) -> tuple[MarketCandle, ...]:
    return tuple(
        MarketCandle(
            opened_at=datetime.fromisoformat(candle.opened_at),
            open=candle.open if candle.open is not None else candle.close,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        for candle in payload.candles
    )


def _build_strategy(*, family: str, strategy_name: str, min_rrr: float) -> object:
    if family == "day_trading":
        return build_day_trading_strategy(strategy_name=strategy_name, min_rrr=min_rrr)
    if family == "scalping":
        return build_scalping_strategy(strategy_name=strategy_name, min_rrr=min_rrr)
    raise ValueError(f"Unsupported strategy family: {family}")


def _evaluate_thresholds(
    *,
    closed_trades: int,
    win_rate: Decimal | None,
    profit_factor: Decimal | None,
    max_drawdown: Decimal | None,
    max_drawdown_pct: Decimal | None = None,
    args: argparse.Namespace | None = None,
    thresholds: BacktestThresholds | None = None,
) -> None:
    resolved_min_closed_trades = args.min_closed_trades if args is not None else thresholds.min_closed_trades if thresholds is not None else None
    resolved_min_win_rate = args.min_win_rate if args is not None else thresholds.min_win_rate if thresholds is not None else None
    resolved_min_profit_factor = args.min_profit_factor if args is not None else thresholds.min_profit_factor if thresholds is not None else None
    resolved_max_drawdown = args.max_drawdown if args is not None else thresholds.max_drawdown if thresholds is not None else None
    resolved_max_drawdown_pct = args.max_drawdown_pct if args is not None else None

    if resolved_min_closed_trades is not None and closed_trades < resolved_min_closed_trades:
        raise BacktestExpectationError(
            f"Expected at least {resolved_min_closed_trades} closed trades, got {closed_trades}.",
        )
    if resolved_min_win_rate is not None and (
        win_rate is None or win_rate < Decimal(str(resolved_min_win_rate))
    ):
        raise BacktestExpectationError(
            f"Expected win rate >= {resolved_min_win_rate}, got {win_rate}.",
        )
    if resolved_min_profit_factor is not None and (
        profit_factor is None or profit_factor < Decimal(str(resolved_min_profit_factor))
    ):
        raise BacktestExpectationError(
            f"Expected profit factor >= {resolved_min_profit_factor}, got {profit_factor}.",
        )
    if resolved_max_drawdown is not None and (
        max_drawdown is None or max_drawdown > Decimal(str(resolved_max_drawdown))
    ):
        raise BacktestExpectationError(
            f"Expected max drawdown <= {resolved_max_drawdown}, got {max_drawdown}.",
        )
    if resolved_max_drawdown_pct is not None and (
        max_drawdown_pct is None or max_drawdown_pct > Decimal(str(resolved_max_drawdown_pct))
    ):
        raise BacktestExpectationError(
            f"Expected max drawdown pct <= {resolved_max_drawdown_pct}, got {max_drawdown_pct}.",
        )


async def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    symbol, interval, lookback_days, candles = _load_candles(args=args)
    strategy = _build_strategy(
        family=args.family,
        strategy_name=args.strategy,
        min_rrr=args.min_rrr,
    )
    simulation_service = SimulationExecutionService(
        max_hold_candles=args.max_hold,
        risk_amount_usd=args.risk_amount,
        fee_rate_per_side=args.fee_rate_per_side,
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=cast("SupportsReplayStrategy", strategy),
        execution_service=cast(
            "SupportsReplayExecutionContext[SimulationExecutionResult]",
            simulation_service,
        ),
    )
    result = await runner.replay(
        HistoricalReplayRequest(symbol=symbol, interval=interval, candles=candles),
    )

    metrics = result.report.metrics
    fees_paid = sum((step.execution.fees_paid for step in result.steps if step.execution is not None), Decimal(0))
    net_pnl = sum(
        (
            step.execution.realized_pnl - step.execution.fees_paid
            for step in result.steps
            if step.execution is not None
        ),
        Decimal(0),
    )
    max_drawdown_pct = None
    if args.risk_amount > 0:
        estimated_equity = Decimal(10000)
        max_drawdown_pct = (metrics.max_drawdown / estimated_equity) if metrics.max_drawdown is not None else None

    print("=== Strategy Backtest ===")
    print(f"Family: {args.family}")
    print(f"Symbol: {symbol}")
    print(f"Interval: {interval}")
    print(f"Strategy: {args.strategy}")
    print(f"Candles: {len(candles)}")
    if lookback_days is not None:
        print(f"Lookback days: {lookback_days}")
    if candles:
        print(f"Range: {candles[0].opened_at.isoformat()} -> {candles[-1].opened_at.isoformat()}")
    print(f"Closed trades: {metrics.closed_trades}")
    print(f"Wins: {metrics.winning_trades}")
    print(f"Losses: {metrics.losing_trades}")
    print(f"Expectancy: {metrics.expectancy}")
    print(f"Win rate: {metrics.win_rate}")
    print(f"Profit factor: {metrics.profit_factor}")
    print(f"Max drawdown: {metrics.max_drawdown}")
    print(f"Max drawdown pct: {max_drawdown_pct}")
    print(f"Net PnL: {net_pnl}")
    print(f"Fees paid: {fees_paid}")
    _evaluate_thresholds(
        closed_trades=metrics.closed_trades,
        win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor,
        max_drawdown=metrics.max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        args=args,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BacktestExpectationError as exc:
        print(f"Backtest expectations failed: {exc}")
        raise SystemExit(1) from exc
