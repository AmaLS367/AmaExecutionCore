# Roadmap: Day Trading Support (15m – 1h timeframes)

**Scope:** Make the bot run autonomously 24/7 on day-trading timeframes without manual
intervention. Every component below builds on the existing codebase and does not require
a rewrite — only additions and targeted fixes.

---

## Prerequisite: Understand the current gap

The execution pipeline is complete and proven:
```
signal → ExecutionService.execute_signal()
            → OrderExecutor.execute()
               → idempotency check
               → position sizing
               → RRR validation
               → exchange constraints
               → Trade persisted
               → REST order placed / shadow-logged
                  → ExchangeSyncEngine (WS or REST reconciliation)
                     → POSITION_OPEN → close → PNL_RECORDED
```

**What is completely absent:** the component that *calls*
`StrategyExecutionService.run()` on a schedule and forwards any resulting signal to
`ExecutionService.execute_signal()`.

Right now a human (or the `/api/signals/execute` HTTP endpoint) triggers the chain.
Without an autonomous loop, the bot never trades by itself.

---

## Phase 1 — Continuous Signal Loop

### 1.1 New module: `backend/signal_loop/`

Create three files:

---

#### `backend/signal_loop/config.py`

Extends `Settings` with loop-specific fields. Add these to `backend/config.py`:

```python
# Signal loop
signal_loop_enabled: bool = False
signal_loop_symbols: list[str] = []           # e.g. ["BTCUSDT", "ETHUSDT"]
signal_loop_interval: str = "15"              # Bybit interval string: 1 3 5 15 30 60 120 240 D
signal_loop_cooldown_seconds: int = 300       # per-symbol cooldown after a trade entry
signal_loop_max_symbols_concurrent: int = 5  # limit parallel strategy evaluations
```

In `.env`:
```
SIGNAL_LOOP_ENABLED=true
SIGNAL_LOOP_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
SIGNAL_LOOP_INTERVAL=15
SIGNAL_LOOP_COOLDOWN_SECONDS=300
```

**Important:** `signal_loop_symbols` needs a custom validator in `Settings` because
Pydantic parses comma-separated env strings only with `List[str]` + correct config.
Use `model_validator` to split by comma if the value arrives as a single string.

---

#### `backend/signal_loop/runner.py`

```python
"""
SignalLoopRunner — autonomous per-symbol strategy evaluation loop.

Responsibilities:
- On each tick: call StrategyExecutionService.run() for each watched symbol
- If a signal is produced: forward it to ExecutionService.execute_signal()
- Track per-symbol cooldown (don't re-enter immediately after a fill)
- Isolate per-symbol errors (one bad symbol must not kill the loop)
- Respect circuit breaker / kill switch (those are already checked inside
  OrderExecutor.execute(), so errors bubble up as SafetyGuardError)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from loguru import logger

from backend.config import settings
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionService
from backend.market_data.contracts import MarketSnapshot


class SupportsExecutionService(Protocol):
    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> object: ...


@dataclass
class _SymbolState:
    symbol: str
    last_entry_at: datetime | None = None

    def is_in_cooldown(self) -> bool:
        if self.last_entry_at is None:
            return False
        elapsed = (datetime.now(UTC) - self.last_entry_at).total_seconds()
        return elapsed < settings.signal_loop_cooldown_seconds

    def record_entry(self) -> None:
        self.last_entry_at = datetime.now(UTC)


class SignalLoopRunner:
    def __init__(
        self,
        *,
        strategy_service: StrategyExecutionService,
        execution_service: SupportsExecutionService,
    ) -> None:
        self._strategy_service = strategy_service
        self._execution_service = execution_service
        self._symbol_states: dict[str, _SymbolState] = {
            sym: _SymbolState(sym)
            for sym in settings.signal_loop_symbols
        }
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        """Entry point — runs until stop() is called."""
        logger.info(
            "SignalLoopRunner started. symbols={} interval={}",
            list(self._symbol_states.keys()),
            settings.signal_loop_interval,
        )
        while not self._stop_event.is_set():
            await self._tick()
            await self._sleep_until_next_candle_close()

    def stop(self) -> None:
        self._stop_event.set()

    async def _tick(self) -> None:
        """Evaluate all symbols concurrently, with a concurrency cap."""
        semaphore = asyncio.Semaphore(settings.signal_loop_max_symbols_concurrent)
        tasks = [
            self._evaluate_symbol(state, semaphore)
            for state in self._symbol_states.values()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _evaluate_symbol(
        self,
        state: _SymbolState,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            if state.is_in_cooldown():
                logger.debug("Symbol {} is in cooldown, skipping.", state.symbol)
                return
            try:
                result = await self._strategy_service.run(
                    StrategyExecutionRequest(
                        symbol=state.symbol,
                        interval=settings.signal_loop_interval,
                    )
                )
                if result.signal is None:
                    logger.debug("No signal for {}.", state.symbol)
                    return

                signal = result.signal
                logger.info(
                    "Signal generated. symbol={} direction={} entry={} stop={} target={}",
                    signal.symbol,
                    signal.direction,
                    signal.entry,
                    signal.stop,
                    signal.target,
                )
                await self._execution_service.execute_signal(
                    signal=ExecuteSignalRequest(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        entry=signal.entry,
                        stop=signal.stop,
                        target=signal.target,
                        reason=signal.reason,
                        strategy_version=signal.strategy_version,
                        indicators_snapshot=signal.indicators_snapshot,
                    )
                )
                state.record_entry()

            except SafetyGuardError as exc:
                # Circuit breaker / kill switch — log and stop the whole loop
                logger.warning(
                    "Safety guard triggered for {}. Halting loop. reason={}",
                    state.symbol,
                    exc,
                )
                self.stop()

            except Exception:
                # Any other error (network, strategy logic) — log and continue other symbols
                logger.exception("Error evaluating symbol {}.", state.symbol)

    async def _sleep_until_next_candle_close(self) -> None:
        """
        Sleep until the next close of the configured candle interval.
        Aligns to wall clock so ticks are synchronized with candle boundaries.

        Bybit interval strings → minutes:
          "1"→1, "3"→3, "5"→5, "15"→15, "30"→30,
          "60"→60, "120"→120, "240"→240, "D"→1440
        """
        interval_minutes = _interval_to_minutes(settings.signal_loop_interval)
        now = datetime.now(UTC)
        total_minutes = now.hour * 60 + now.minute
        elapsed_in_period = total_minutes % interval_minutes
        minutes_to_next = interval_minutes - elapsed_in_period
        # Add a small buffer so candle is confirmed closed on Bybit's side
        wait_seconds = minutes_to_next * 60 - now.second + 2
        if wait_seconds <= 0:
            wait_seconds = interval_minutes * 60
        logger.debug(
            "Next tick in {}s (interval={}m).", wait_seconds, interval_minutes
        )
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=wait_seconds,
            )
        except asyncio.TimeoutError:
            pass  # normal — woke up at the right time


def _interval_to_minutes(interval: str) -> int:
    _MAP = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
            "60": 60, "120": 120, "240": 240, "D": 1440}
    if interval not in _MAP:
        raise ValueError(f"Unknown interval: {interval!r}")
    return _MAP[interval]
```

---

#### Integration in `backend/main.py`

Add to the `lifespan` context manager:

```python
from backend.signal_loop.runner import SignalLoopRunner
from backend.market_data.bybit_spot import BybitSpotSnapshotProvider
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.service import StrategyExecutionService

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ... existing sync engine wiring ...

    signal_loop_task: asyncio.Task | None = None
    if settings.signal_loop_enabled:
        strategy = EMACrossoverStrategy()
        snapshot_provider = BybitSpotSnapshotProvider(rest_client=app.state.rest_client)
        strategy_service = StrategyExecutionService(
            snapshot_provider=snapshot_provider,
            strategy=strategy,
        )
        loop_runner = SignalLoopRunner(
            strategy_service=strategy_service,
            execution_service=app.state.execution_service,
        )
        app.state.signal_loop = loop_runner
        signal_loop_task = asyncio.create_task(loop_runner.run_forever())
        logger.info("Signal loop started.")

    yield

    if signal_loop_task is not None:
        loop_runner.stop()
        await asyncio.gather(signal_loop_task, return_exceptions=True)
        logger.info("Signal loop stopped.")
    # ... existing stop calls ...
```

---

### 1.2 What the loop does NOT do (important constraints)

- It does **not** manage position closing. That is handled by `ExchangeSyncEngine`
  (WS events) + `PositionManagerService` (manual close endpoint). The loop only opens.
- It does **not** retry on `SafetyGuardError` — those are intentional halts.
- It does **not** open a second position on the same symbol while one is open.
  The existing `OrderExecutor` guard (`is_order_already_submitted`) + idempotency
  fingerprinting handle this. The cooldown is an extra layer above that.

---

### 1.3 Unit tests needed

`tests/signal_loop/test_runner.py`:

- `test_tick_calls_strategy_for_each_symbol` — mock strategy service, verify N calls
- `test_tick_skips_symbol_in_cooldown` — record_entry(), then tick, verify skipped
- `test_tick_isolates_per_symbol_errors` — one symbol throws, others still called
- `test_tick_stops_loop_on_safety_guard_error` — SafetyGuardError → stop_event set
- `test_sleep_until_next_candle_close_alignment` — verify wait math for common intervals

---

## Phase 2 — Volume field on `MarketCandle`

This is a one-line data change, but it cascades through three files.

### 2.1 `backend/market_data/contracts.py`

```python
@dataclass(slots=True, frozen=True)
class MarketCandle:
    opened_at: datetime
    high: float
    low: float
    close: float
    volume: float = 0.0   # NEW — default 0.0 keeps existing test code valid
```

Add to `MarketSnapshot`:
```python
@property
def volumes(self) -> tuple[float, ...]:
    return tuple(candle.volume for candle in self.candles)
```

### 2.2 `backend/market_data/bybit_spot.py`

`BybitKline` already carries `volume` (see `rest.py:22`). Wire it through:

```python
candles = tuple(
    MarketCandle(
        opened_at=kline.start_time,
        high=kline.high_price,
        low=kline.low_price,
        close=kline.close_price,
        volume=kline.volume,     # NEW
    )
    for kline in ordered_klines
)
```

### 2.3 Backtest replay

`replay_runner.py:191-195` constructs `MarketCandle` from `request.candles` directly —
no change needed since `volume` defaults to `0.0`. Backtest callers can pass real volume
data when available.

---

## Phase 3 — Config Tuning for Day Trading

The current defaults work but need re-examination for day trading.

| Setting | Current | Recommended for 15m-1h | Reason |
|---------|---------|------------------------|--------|
| `risk_per_trade_pct` | 0.01 (1%) | 0.005–0.01 (0.5–1%) | 1% is fine; 0.5% if trading 3+ symbols |
| `min_rrr` | 2.0 | 2.0 | Correct for day trading |
| `max_open_positions` | 1 | 1–3 | Raise only when multi-symbol loop is running |
| `max_total_risk_exposure_pct` | 0.03 (3%) | 0.03 | Fine |
| `max_daily_loss_pct` | 0.03 (3%) | 0.02 (2%) | Tighten — day trading produces more trades |
| `max_consecutive_losses` | 3 | 3 | Fine |
| `cooldown_hours` | 4 | 2 | 4h is too long — misses entire trading sessions |
| `hard_pause_consecutive_losses` | 5 | 4 | Tighten |
| `order_mode` | maker_preferred | maker_preferred | Limit orders fine for 15m signals |

Add a new field that current code lacks:
```python
max_trades_per_day: int = 10   # hard daily cap to prevent runaway loop
```

`CircuitBreaker.check()` needs to read `DailyStat.total_trades` and raise if exceeded.
Currently it only checks loss pcts — adding trade count is a small addition.

---

## Phase 4 — Honest Backtesting

### 4.1 The problem with the current `HistoricalReplayRunner`

`replay_runner.py:_build_report()` computes `realized_pnl` by reading
`step.execution.realized_pnl` — but in production the `ExecutionService` returns
an `ExecutionResult` which has `status`, `trade_id` etc., **not** `realized_pnl`.
The only object that eventually has `realized_pnl` is the `Trade` model, populated
hours later by `ExchangeSyncEngine` when the position closes.

For backtesting we need a fake execution service that simulates trade outcomes
using the *subsequent* candles.

### 4.2 `backend/backtest/simulation_execution_service.py`

```python
"""
SimulationExecutionService — paper-trades signals against historical candles.

Given a signal (entry, stop, target) and a future candle stream, determines
whether the stop or target was touched first and records realized PNL.

Rules:
- Entry is assumed filled at the signal's entry price (next candle open in reality,
  but for a benchmark we use the signaled price — note this as a bias).
- Stop and target are checked against subsequent candles' high/low:
    LONG:  stop hit if candle.low <= stop_price
           target hit if candle.high >= target_price
    SHORT: stop hit if candle.high >= stop_price
           target hit if candle.low <= target_price
- The first candle that touches either level wins.
- If neither is touched within `max_hold_candles`, the trade is closed at
  the last candle's close (treated as a timeout / manual close).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest


@dataclass(slots=True, frozen=True)
class SimulatedExecutionResult:
    realized_pnl: Decimal      # positive = win, negative = loss
    slippage: Decimal          # always 0.0 in simulation (no real fill)
    exit_reason: str           # "tp_hit" | "sl_hit" | "timeout"
    hold_candles: int


class SimulationExecutionService:
    """
    Does NOT touch the database. Designed for HistoricalReplayRunner.

    Usage:
        future_candles_map = {"BTCUSDT": [candle_t+1, candle_t+2, ...]}
        service = SimulationExecutionService(future_candles_map, max_hold_candles=20)
        runner = HistoricalReplayRunner(strategy=strategy, execution_service=service)
    """

    def __init__(
        self,
        future_candles: dict[str, list[MarketCandle]],
        max_hold_candles: int = 20,
        risk_amount_usd: float = 100.0,  # fixed notional per trade for metrics
    ) -> None:
        self._future_candles = future_candles
        self._max_hold = max_hold_candles
        self._risk_amount = Decimal(str(risk_amount_usd))

    async def execute_signal(
        self, *, signal: ExecuteSignalRequest
    ) -> SimulatedExecutionResult:
        candles = self._future_candles.get(signal.symbol, [])
        entry = Decimal(str(signal.entry))
        stop = Decimal(str(signal.stop))
        target = Decimal(str(signal.target))
        is_long = signal.direction == "long"
        risk = abs(entry - stop)
        reward = abs(target - entry)

        for i, candle in enumerate(candles[: self._max_hold]):
            low = Decimal(str(candle.low))
            high = Decimal(str(candle.high))

            if is_long:
                if low <= stop:
                    return SimulatedExecutionResult(
                        realized_pnl=-self._risk_amount,
                        slippage=Decimal("0"),
                        exit_reason="sl_hit",
                        hold_candles=i + 1,
                    )
                if high >= target:
                    rrr = reward / risk if risk else Decimal("0")
                    return SimulatedExecutionResult(
                        realized_pnl=self._risk_amount * rrr,
                        slippage=Decimal("0"),
                        exit_reason="tp_hit",
                        hold_candles=i + 1,
                    )
            else:
                if high >= stop:
                    return SimulatedExecutionResult(
                        realized_pnl=-self._risk_amount,
                        slippage=Decimal("0"),
                        exit_reason="sl_hit",
                        hold_candles=i + 1,
                    )
                if low <= target:
                    rrr = reward / risk if risk else Decimal("0")
                    return SimulatedExecutionResult(
                        realized_pnl=self._risk_amount * rrr,
                        slippage=Decimal("0"),
                        exit_reason="tp_hit",
                        hold_candles=i + 1,
                    )

        # Timeout — close at last available close price
        if candles:
            last_close = Decimal(str(candles[min(self._max_hold, len(candles)) - 1].close))
            if is_long:
                pnl = (last_close - entry) / risk * self._risk_amount
            else:
                pnl = (entry - last_close) / risk * self._risk_amount
            return SimulatedExecutionResult(
                realized_pnl=pnl,
                slippage=Decimal("0"),
                exit_reason="timeout",
                hold_candles=self._max_hold,
            )

        return SimulatedExecutionResult(
            realized_pnl=Decimal("0"),
            slippage=Decimal("0"),
            exit_reason="timeout",
            hold_candles=0,
        )
```

### 4.3 `scripts/run_backtest.py`

```python
"""
CLI backtest runner.

Usage:
    uv run python scripts/run_backtest.py --symbol BTCUSDT --interval 15 --candles 2000

Fetches historical klines from Bybit (paginated, max 1000/request),
runs HistoricalReplayRunner with SimulationExecutionService, prints report.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from backend.bybit_client.rest import BybitRESTClient
from backend.market_data.bybit_spot import BybitSpotSnapshotProvider
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.backtest.replay_runner import HistoricalReplayRequest, HistoricalReplayRunner
from backend.backtest.simulation_execution_service import SimulationExecutionService


def fetch_candles(client, symbol: str, interval: str, total: int) -> list[MarketCandle]:
    """Paginate Bybit klines (max 1000 per request) oldest-first."""
    candles: list[MarketCandle] = []
    end_time = None
    while len(candles) < total:
        batch_limit = min(1000, total - len(candles))
        klines = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=batch_limit,
            # end_time=end_time,  # add if pybit supports cursor-based pagination
        )
        if not klines:
            break
        for k in sorted(klines, key=lambda x: x.start_time):
            candles.append(MarketCandle(
                opened_at=k.start_time,
                high=k.high_price,
                low=k.low_price,
                close=k.close_price,
                volume=k.volume,
            ))
        end_time = klines[0].start_time  # oldest in batch → use as upper bound for next
        if len(klines) < batch_limit:
            break
    return sorted(candles, key=lambda c: c.opened_at)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15")
    parser.add_argument("--candles", type=int, default=2000)
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--rrr", type=float, default=2.0)
    parser.add_argument("--max-hold", type=int, default=20,
                        help="Max candles to hold before closing at market")
    args = parser.parse_args()

    client = BybitRESTClient()
    print(f"Fetching {args.candles} candles for {args.symbol} {args.interval}m...")
    candles = fetch_candles(client, args.symbol, args.interval, args.candles)
    print(f"  Got {len(candles)} candles: {candles[0].opened_at} → {candles[-1].opened_at}")

    strategy = EMACrossoverStrategy(fast=args.fast, slow=args.slow, min_rrr=args.rrr)
    # Future candles for each step are the candles after the signal candle
    future_map = {args.symbol: candles}
    sim_service = SimulationExecutionService(
        future_candles=future_map,
        max_hold_candles=args.max_hold,
    )

    runner = HistoricalReplayRunner(strategy=strategy, execution_service=sim_service)
    result = await runner.replay(
        HistoricalReplayRequest(
            symbol=args.symbol,
            interval=args.interval,
            candles=tuple(candles),
        )
    )

    m = result.report.metrics
    print("\n=== Backtest Report ===")
    print(f"Symbol:         {args.symbol}  Interval: {args.interval}m")
    print(f"Strategy:       EMA {args.fast}/{args.slow}  min_rrr={args.rrr}")
    print(f"Candles:        {len(candles)}")
    print(f"Total steps:    {len(result.steps)}")
    print(f"Closed trades:  {m.closed_trades}")
    print(f"Wins:           {m.winning_trades}")
    print(f"Losses:         {m.losing_trades}")
    wr = f"{float(m.win_rate):.1%}" if m.win_rate is not None else "N/A"
    print(f"Win rate:       {wr}")
    exp = f"{float(m.expectancy):.2f} USD" if m.expectancy is not None else "N/A"
    print(f"Expectancy:     {exp}")
    pf = f"{float(m.profit_factor):.2f}" if m.profit_factor is not None else "N/A"
    print(f"Profit factor:  {pf}")
    dd = f"{float(m.max_drawdown):.2f} USD" if m.max_drawdown is not None else "N/A"
    print(f"Max drawdown:   {dd}")
    print()
    print("NOTE: This backtest assumes entry fills exactly at signal price.")
    print("Real fills have slippage. Use as directional signal quality indicator only.")


if __name__ == "__main__":
    asyncio.run(main())
```

### 4.4 Known biases in this backtest model

These must be documented and understood before trusting results:

1. **Entry price bias** — we assume fill at exactly the signal's entry price. In reality
   market orders fill at the next candle's open, which is almost always higher (LONG) or
   lower (SHORT) due to candle close → open gap.

2. **Candle resolution** — we only check stop/target at candle highs/lows. The actual
   sequence within a candle (did price hit low or high first?) is unknowable from OHLC.
   We treat a candle that touches both stop and target as hitting stop first (conservative).

3. **No fees** — Bybit spot taker fee is 0.1% per side = 0.2% round-trip. On a 1:2 RRR
   trade this is material. Subtract `2 * 0.001 * entry * qty` from each `realized_pnl`.

4. **Look-ahead in `future_map`** — the current `SimulationExecutionService` receives
   the full candle list upfront and slices by step index. The `HistoricalReplayRunner`
   needs to pass the correct future slice (candles *after* the signal candle) to avoid
   look-ahead bias. This requires a small refactor to `HistoricalReplayRunner.replay()`:
   pass `step_index` into `execute_signal` so the service can slice correctly.

---

## Phase 5 — Strategy Quality (EMA 9/21 Assessment)

### Why EMA crossover underperforms on 15m

EMA 9/21 was designed for 4h–daily swing trading. On 15m:
- Signal arrives late (EMA is a lagging indicator by definition)
- The crossover often occurs mid-move, not at the start
- False crossovers in ranging markets are very frequent
- Win rate typically 35–45%, which requires RRR > 2.5 to be profitable

The strategy is adequate as a **placeholder** while the infrastructure is built,
but should be replaced or supplemented.

### Removed candidate: RSI + EMA Confluence

The RSI + EMA confluence candidate and its spot-v2 variant were removed after fixture validation failed to produce a viable trade distribution. Do not restore this strategy without a fresh feasibility sweep that passes per-symbol trade count, win-rate, profit-factor, and positive-expectancy thresholds before implementation.

---

## Phase 6 — `max_trades_per_day` Circuit Breaker Addition

Currently `CircuitBreaker.check()` in `backend/safety_guard/circuit_breaker.py`
enforces:
- Daily loss pct limit (`max_daily_loss_pct`)
- Weekly loss pct limit (`max_weekly_loss_pct`)
- Consecutive loss count (`max_consecutive_losses`, `hard_pause_consecutive_losses`)

It does **not** check trade count. For an autonomous loop, runaway signal generation
(e.g., bug in strategy producing signals every candle) would hit the exchange
relentlessly without this guard.

Add to `Settings`:
```python
max_trades_per_day: int = 10
```

Add to `CircuitBreaker.check()` after the existing checks:
```python
if stat.total_trades >= settings.max_trades_per_day:
    raise DailyLossLimitError(
        f"Daily trade cap of {settings.max_trades_per_day} reached."
    )
```

`DailyStat.total_trades` is already incremented by `circuit_breaker.record_loss()` and
`circuit_breaker.record_win()` — but those are called *after* a close, not on entry.
The correct place is `OrderExecutor.execute()` after the Trade is persisted with
`RISK_CALCULATED` status: call `await circuit_breaker.increment_trade_count(session)`.

This requires a small addition to `CircuitBreaker`:
```python
async def increment_trade_count(self, session: AsyncSession) -> None:
    stat = await self._get_or_create_today(session)
    stat.total_trades = (stat.total_trades or 0) + 1
```

---

## Summary: Execution Order for Day Trading

```
Week 1
  Day 1–2:   Phase 1 — signal loop (runner.py + lifespan wiring + config)
  Day 3:     Phase 2 — volume field (MarketCandle + BybitSpotSnapshotProvider)
  Day 4:     Phase 6 — max_trades_per_day guard (safety net before going live)
  Day 5:     Manual testing on testnet with SIGNAL_LOOP_ENABLED=true

Week 2
  Day 1–2:   Phase 4 — simulation backtest (SimulationExecutionService + scripts/)
  Day 3–5:   Phase 5 — RSI+EMA strategy + backtest validation

Week 3
  Day 1:     Phase 3 — config tuning (document recommended values in .env.example)
  Day 2–3:   End-to-end test on testnet for 2 full trading sessions
  Day 4–5:   Review DailyStat metrics, tune risk pcts
```

---

## Checklist before enabling `SIGNAL_LOOP_ENABLED=true` on mainnet

- [ ] Signal loop runs on testnet for ≥48 hours without crashes
- [ ] Circuit breaker fires correctly and stops loop when triggered
- [ ] Kill switch endpoint (`POST /safety/kill-switch`) tested
- [ ] `max_trades_per_day` guard verified (set to 1, confirm second trade blocked)
- [ ] Per-symbol cooldown verified (confirm symbol skipped after entry)
- [ ] DB trade count reviewed after 48h testnet run — no stuck trades
- [ ] Backtest on 2000+ candles shows expectancy > 0 (otherwise change strategy before going live)
