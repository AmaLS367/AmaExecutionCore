from __future__ import annotations
import asyncio
from decimal import Decimal
from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner, SimulationExecutionService
from backend.bybit_client.rest import BybitRESTClient
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy

FEE_RATE = Decimal("0.001")


def fetch_candles(client, *, symbol, interval, total):
    candles, end_cursor = [], None
    while len(candles) < total:
        batch = client.get_klines(symbol=symbol, interval=interval,
                                   limit=min(1000, total - len(candles)),
                                   category="spot", end=end_cursor)
        if not batch:
            break
        ordered = sorted(batch, key=lambda c: c.start_time)
        candles.extend(MarketCandle(opened_at=c.start_time, high=c.high_price,
                                    low=c.low_price, close=c.close_price,
                                    volume=c.volume) for c in ordered)
        end_cursor = int(ordered[0].start_time.timestamp() * 1000) - 1
        if len(batch) < min(1000, total - len(candles)):
            break
    deduped = {c.opened_at: c for c in candles}
    return tuple(sorted(deduped.values(), key=lambda c: c.opened_at))[-total:]


async def run_sim(deposit: float, risk_pct: float, candles_data: tuple):
    risk_usd = deposit * risk_pct / 100
    strategy = VWAPReversionStrategy()
    sim_svc = SimulationExecutionService(max_hold_candles=20, risk_amount_usd=risk_usd)
    runner = HistoricalReplayRunner(strategy=strategy, execution_service=sim_svc)
    result = await runner.replay(
        HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=candles_data)
    )

    equity = Decimal(str(deposit))
    peak = equity
    max_dd = Decimal("0")
    trades = []
    for step in result.steps:
        ex = step.execution
        if ex is None:
            continue
        pnl = ex.realized_pnl
        fee = Decimal(str(risk_usd)) * FEE_RATE * 2
        net = pnl - fee
        equity += net
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        trades.append(net)

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total_trades = len(trades)
    net_profit = equity - Decimal(str(deposit))
    win_rate = len(wins) / total_trades * 100 if total_trades else 0

    return {
        "deposit": deposit,
        "risk_pct": risk_pct,
        "risk_usd": risk_usd,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "final_equity": float(equity),
        "net_profit": float(net_profit),
        "max_drawdown": float(max_dd),
        "max_drawdown_pct": float(max_dd / Decimal(str(deposit)) * 100),
        "roi": float(net_profit / Decimal(str(deposit)) * 100),
    }


async def main():
    client = BybitRESTClient()
    print("Fetching 3 months of candles...")
    candles = fetch_candles(client, symbol="BTCUSDT", interval="5", total=25000)
    start_date = candles[0].opened_at.strftime("%Y-%m-%d")
    end_date = candles[-1].opened_at.strftime("%Y-%m-%d")
    print(f"Period: {start_date} -> {end_date}  ({len(candles)} candles)")
    print()

    for deposit in [100.0, 200.0]:
        r = await run_sim(deposit=deposit, risk_pct=1.0, candles_data=candles)
        adj_profit = r["net_profit"] * 0.85
        print("=" * 55)
        print(f"  DEPOSIT ${deposit:.0f}  |  Risk {r['risk_pct']}% = ${r['risk_usd']:.2f}/trade")
        print("=" * 55)
        print(f"  Trades:        {r['total_trades']} ({r['wins']}W / {r['losses']}L)")
        print(f"  Win rate:      {r['win_rate']:.1f}%")
        print(f"  Final equity:  ${r['final_equity']:.2f}")
        print(f"  Net profit:    +${r['net_profit']:.2f}  (+{r['roi']:.1f}% ROI)")
        print(f"  Max drawdown:  ${r['max_drawdown']:.2f} ({r['max_drawdown_pct']:.1f}% of deposit)")
        print()
        print("  --- Pessimistic (-15% optimism discount) ---")
        print(f"  Adj profit:    +${adj_profit:.2f}  (+{adj_profit/deposit*100:.1f}% ROI)")
        print(f"  Final account: ${deposit + adj_profit:.2f}")
        print()


asyncio.run(main())
