from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import cast

from backend.backtest import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.backtest.replay_runner import SupportsReplayExecutionContext
from backend.bybit_client.rest import BybitRESTClient
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy

FEE_RATE_PER_SIDE = Decimal("0.001")


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
        end_cursor = int(ordered_batch[0].start_time.timestamp() * 1000) - 1
        if len(batch) < min(1000, total - len(candles)):
            break
    deduped = {candle.opened_at: candle for candle in candles}
    return tuple(sorted(deduped.values(), key=lambda candle: candle.opened_at))[-total:]


def _calculate_max_drawdown(pnls: list[Decimal]) -> Decimal:
    running_equity = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    for pnl in pnls:
        running_equity += pnl
        peak = max(peak, running_equity)
        drawdown = peak - running_equity
        max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


async def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the default scalping strategy.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5")
    parser.add_argument("--candles", type=int, default=9000)
    parser.add_argument("--risk-amount", type=float, default=100.0)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--output", default="validation_result.json")
    args = parser.parse_args()

    client = BybitRESTClient()
    candles = _fetch_candles(client, symbol=args.symbol, interval=args.interval, total=args.candles)
    strategy = VWAPReversionStrategy()
    simulation_service = SimulationExecutionService(
        max_hold_candles=args.max_hold,
        risk_amount_usd=args.risk_amount,
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=strategy,
        execution_service=cast(
            "SupportsReplayExecutionContext[SimulationExecutionResult]",
            simulation_service,
        ),
    )
    result = await runner.replay(
        HistoricalReplayRequest(symbol=args.symbol, interval=args.interval, candles=candles),
    )

    fee_amount = Decimal(str(args.risk_amount)) * (FEE_RATE_PER_SIDE * 2)
    net_trade_pnls: list[Decimal] = []
    for step in result.steps:
        execution = step.execution
        if execution is None:
            continue
        realized_pnl = getattr(execution, "realized_pnl", None)
        if not isinstance(realized_pnl, Decimal):
            continue
        net_trade_pnls.append(realized_pnl - fee_amount)

    closed_trades = len(net_trade_pnls)
    winning_trades = len([pnl for pnl in net_trade_pnls if pnl > 0])
    win_rate = (Decimal(winning_trades) / Decimal(closed_trades)) if closed_trades else Decimal(0)
    expectancy = (sum(net_trade_pnls, Decimal(0)) / Decimal(closed_trades)) if closed_trades else Decimal(0)
    max_drawdown = _calculate_max_drawdown(net_trade_pnls) if net_trade_pnls else Decimal(0)
    avg_risk_amount = Decimal(str(args.risk_amount))

    passed = all(
        (
            win_rate > Decimal("0.55"),
            expectancy > Decimal(0),
            max_drawdown < (avg_risk_amount * Decimal(10)),
            closed_trades >= 30,
        ),
    )

    payload = {
        "symbol": args.symbol,
        "interval": args.interval,
        "candles": len(candles),
        "closed_trades": closed_trades,
        "winning_trades": winning_trades,
        "win_rate": str(win_rate),
        "expectancy": str(expectancy),
        "max_drawdown": str(max_drawdown),
        "avg_risk_amount": str(avg_risk_amount),
        "passed": passed,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
