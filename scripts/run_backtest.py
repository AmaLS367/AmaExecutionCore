from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from backend.bybit_client.rest import BybitRESTClient
    from backend.market_data.contracts import MarketCandle


@dataclass(slots=True, frozen=True)
class FixtureCandle:
    opened_at: str
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(slots=True, frozen=True)
class FixturePayload:
    symbol: str
    interval: str
    candles: tuple[FixtureCandle, ...]


@dataclass(slots=True, frozen=True)
class BacktestThresholds:
    min_closed_trades: int | None = None
    min_win_rate: float | None = None
    min_profit_factor: float | None = None
    max_drawdown: float | None = None


class BacktestExpectationError(RuntimeError):
    """Raised when backtest metrics do not meet the requested thresholds."""


def _fetch_candles(
    client: BybitRESTClient,
    *,
    symbol: str,
    interval: str,
    total: int,
) -> tuple[MarketCandle, ...]:
    from backend.market_data.contracts import MarketCandle

    candles: list[MarketCandle] = []
    end_cursor: int | None = None
    while len(candles) < total:
        batch = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=min(1000, total - len(candles)),
            category="spot",
            end=end_cursor,
        )
        if not batch:
            break
        ordered_batch = sorted(batch, key=lambda candle: candle.start_time)
        candles.extend(
            MarketCandle(
                opened_at=item.start_time,
                high=item.high_price,
                low=item.low_price,
                close=item.close_price,
                volume=item.volume,
            )
            for item in ordered_batch
        )
        oldest_candle = ordered_batch[0]
        end_cursor = int(oldest_candle.start_time.timestamp() * 1000) - 1
        if len(batch) < min(1000, total - len(candles)):
            break
    deduped = {candle.opened_at: candle for candle in candles}
    return tuple(sorted(deduped.values(), key=lambda candle: candle.opened_at))[-total:]


def _extract_trade_pnls(steps: Iterable[object]) -> list[Decimal]:
    pnls: list[Decimal] = []
    for step in steps:
        execution = getattr(step, "execution", None)
        if execution is None:
            continue
        realized_pnl = getattr(execution, "realized_pnl", None)
        if isinstance(realized_pnl, Decimal):
            pnls.append(realized_pnl)
    return pnls


def _load_fixture_payload(path: Path) -> FixturePayload:
    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("Fixture payload must be a JSON object.")

    symbol = str(raw_payload.get("symbol", "")).strip().upper()
    interval = str(raw_payload.get("interval", "")).strip()
    raw_candles = raw_payload.get("candles")
    if not symbol:
        raise ValueError("Fixture payload must define a non-empty symbol.")
    if not interval:
        raise ValueError("Fixture payload must define a non-empty interval.")
    if not isinstance(raw_candles, list) or not raw_candles:
        raise ValueError("Fixture payload must define a non-empty candles list.")

    candles: list[FixtureCandle] = []
    for index, raw_candle in enumerate(raw_candles):
        if not isinstance(raw_candle, dict):
            raise ValueError(f"Fixture candle #{index} must be a JSON object.")
        candles.append(
            FixtureCandle(
                opened_at=str(raw_candle["opened_at"]),
                high=float(raw_candle["high"]),
                low=float(raw_candle["low"]),
                close=float(raw_candle["close"]),
                volume=float(raw_candle.get("volume", 0.0)),
            ),
        )
    return FixturePayload(symbol=symbol, interval=interval, candles=tuple(candles))


def _coerce_fixture_candles(candles: tuple[FixtureCandle, ...]) -> tuple[MarketCandle, ...]:
    from datetime import datetime

    from backend.market_data.contracts import MarketCandle

    return tuple(
        MarketCandle(
            opened_at=datetime.fromisoformat(candle.opened_at.replace("Z", "+00:00")),
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        for candle in candles
    )


def _evaluate_thresholds(
    *,
    closed_trades: int,
    win_rate: float | None,
    profit_factor: float | None,
    max_drawdown: float | None,
    thresholds: BacktestThresholds,
) -> None:
    if thresholds.min_closed_trades is not None and closed_trades < thresholds.min_closed_trades:
        raise BacktestExpectationError(
            f"Expected at least {thresholds.min_closed_trades} closed trades, got {closed_trades}.",
        )
    if thresholds.min_win_rate is not None:
        if win_rate is None or win_rate < thresholds.min_win_rate:
            raise BacktestExpectationError(
                f"Expected win rate >= {thresholds.min_win_rate}, got {win_rate}.",
            )
    if thresholds.min_profit_factor is not None:
        if profit_factor is None or profit_factor < thresholds.min_profit_factor:
            raise BacktestExpectationError(
                f"Expected profit factor >= {thresholds.min_profit_factor}, got {profit_factor}.",
            )
    if thresholds.max_drawdown is not None:
        if max_drawdown is None or max_drawdown > thresholds.max_drawdown:
            raise BacktestExpectationError(
                f"Expected max drawdown <= {thresholds.max_drawdown}, got {max_drawdown}.",
            )


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a historical day-trading backtest.")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--symbol")
    parser.add_argument("--interval")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--strategy", default="rsi_ema", choices=("rsi_ema", "ema_crossover"))
    parser.add_argument("--min-rrr", type=float, default=1.5)
    parser.add_argument("--risk-amount", type=float, default=100.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--min-closed-trades", type=int)
    parser.add_argument("--min-win-rate", type=float)
    parser.add_argument("--min-profit-factor", type=float)
    parser.add_argument("--max-drawdown", type=float)
    return parser


async def main() -> None:
    from backend.backtest import (
        HistoricalReplayRequest,
        HistoricalReplayRunner,
        SimulationExecutionResult,
        SimulationExecutionService,
    )
    from backend.backtest.replay_runner import SupportsReplayExecutionContext
    from backend.bybit_client.rest import BybitRESTClient
    from backend.strategy_engine.factory import build_day_trading_strategy

    args = _build_parser().parse_args()
    thresholds = BacktestThresholds(
        min_closed_trades=args.min_closed_trades,
        min_win_rate=args.min_win_rate,
        min_profit_factor=args.min_profit_factor,
        max_drawdown=args.max_drawdown,
    )

    fixture_payload: FixturePayload | None = None
    if args.fixture is not None:
        fixture_payload = _load_fixture_payload(args.fixture)
        candles = _coerce_fixture_candles(fixture_payload.candles)
    else:
        client = BybitRESTClient()
        candles = _fetch_candles(
            client,
            symbol=args.symbol or "BTCUSDT",
            interval=args.interval or "15",
            total=args.candles,
        )

    symbol = args.symbol or (fixture_payload.symbol if fixture_payload is not None else "BTCUSDT")
    interval = args.interval or (fixture_payload.interval if fixture_payload is not None else "15")
    strategy = build_day_trading_strategy(strategy_name=args.strategy, min_rrr=args.min_rrr)
    simulation_service = SimulationExecutionService(
        max_hold_candles=args.max_hold,
        risk_amount_usd=args.risk_amount,
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=strategy,
        execution_service=cast(
            SupportsReplayExecutionContext[SimulationExecutionResult],
            simulation_service,
        ),
    )
    result = await runner.replay(
        HistoricalReplayRequest(symbol=symbol, interval=interval, candles=candles),
    )

    pnls = _extract_trade_pnls(result.steps)
    print("=== Day-Trading Backtest ===")
    print(f"Symbol: {symbol}")
    print(f"Interval: {interval}")
    print(f"Strategy: {args.strategy}")
    print(f"Candles: {len(candles)}")
    if candles:
        print(f"Range: {candles[0].opened_at.isoformat()} -> {candles[-1].opened_at.isoformat()}")
    print(f"Closed trades: {result.report.metrics.closed_trades}")
    print(f"Wins: {result.report.metrics.winning_trades}")
    print(f"Losses: {result.report.metrics.losing_trades}")
    print(f"Expectancy: {result.report.metrics.expectancy}")
    print(f"Win rate: {result.report.metrics.win_rate}")
    print(f"Profit factor: {result.report.metrics.profit_factor}")
    print(f"Max drawdown: {result.report.metrics.max_drawdown}")
    if pnls:
        print(f"Net PnL sum: {sum(pnls, Decimal('0'))}")
    _evaluate_thresholds(
        closed_trades=result.report.metrics.closed_trades,
        win_rate=_to_float(result.report.metrics.win_rate),
        profit_factor=_to_float(result.report.metrics.profit_factor),
        max_drawdown=_to_float(result.report.metrics.max_drawdown),
        thresholds=thresholds,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BacktestExpectationError as exc:
        print(f"Backtest expectations failed: {exc}")
        raise SystemExit(1) from exc
