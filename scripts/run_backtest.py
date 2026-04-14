from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
from decimal import Decimal

from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner, SimulationExecutionService
from backend.bybit_client.rest import BybitRESTClient
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.factory import build_day_trading_strategy


def _fetch_candles(
    client: BybitRESTClient,
    *,
    symbol: str,
    interval: str,
    total: int,
) -> tuple[MarketCandle, ...]:
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a historical day-trading backtest.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--strategy", default="rsi_ema", choices=("rsi_ema", "ema_crossover"))
    parser.add_argument("--min-rrr", type=float, default=1.5)
    parser.add_argument("--risk-amount", type=float, default=100.0)
    parser.add_argument("--max-hold", type=int, default=20)
    args = parser.parse_args()

    client = BybitRESTClient()
    candles = _fetch_candles(
        client,
        symbol=args.symbol,
        interval=args.interval,
        total=args.candles,
    )
    strategy = build_day_trading_strategy(strategy_name=args.strategy, min_rrr=args.min_rrr)
    simulation_service = SimulationExecutionService(
        max_hold_candles=args.max_hold,
        risk_amount_usd=args.risk_amount,
    )
    runner = HistoricalReplayRunner(strategy=strategy, execution_service=simulation_service)
    result = await runner.replay(
        HistoricalReplayRequest(symbol=args.symbol, interval=args.interval, candles=candles)
    )

    pnls = _extract_trade_pnls(result.steps)
    print("=== Day-Trading Backtest ===")
    print(f"Symbol: {args.symbol}")
    print(f"Interval: {args.interval}")
    print(f"Strategy: {args.strategy}")
    print(f"Candles: {len(candles)}")
    if candles:
        print(f"Range: {candles[0].opened_at.isoformat()} -> {candles[-1].opened_at.isoformat()}")
    print(f"Closed trades: {result.report.metrics.closed_trades}")
    print(f"Wins: {result.report.metrics.winning_trades}")
    print(f"Losses: {result.report.metrics.losing_trades}")
    print(f"Expectancy: {result.report.metrics.expectancy}")
    print(f"Profit factor: {result.report.metrics.profit_factor}")
    print(f"Max drawdown: {result.report.metrics.max_drawdown}")
    if pnls:
        print(f"Net PnL sum: {sum(pnls, Decimal('0'))}")


if __name__ == "__main__":
    asyncio.run(main())
