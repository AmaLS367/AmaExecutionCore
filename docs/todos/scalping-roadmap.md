# Roadmap: Scalping Support (1m – 5m timeframes)

**Scope:** Upgrade the bot from REST-polled day trading to event-driven scalping on
1m–5m candles. This is a significant architectural addition on top of the day trading
roadmap — do **not** start this until Phases 1–3 of the day trading roadmap are complete.

---

## Why scalping requires different infrastructure

The day trading loop works like this:
```
Timer fires → REST call (get_klines) → strategy → signal? → execute
```

On 15m candles, a 1–3s REST round-trip is insignificant.

On 1m candles, the same approach has two fatal problems:

1. **Timing drift** — REST calls take 300ms–2s depending on network. At 1m candles,
   your tick fires at candle close + 2s. By the time a market order hits the exchange,
   the setup is already 3–5 seconds stale. On volatile 1m candles this is meaningful.

2. **Rate limits** — Running 5 symbols × every 60s = 5 REST calls/minute just for
   candles. Bybit's rate limit on `GET /v5/market/kline` is generous (600 req/5min) but
   this eats into your budget when combined with order management.

The solution is **WebSocket-based candle feed** that pushes confirmed candle closes
in real time. No polling. No drift.

---

## Phase 1 — WebSocket Market Data Feed

### 1.1 `backend/market_data/bybit_ws_feed.py`

Bybit's public WebSocket `kline.{interval}.{symbol}` topic sends a message on every
tick update. The `confirm` field becomes `true` on the final tick (candle closed).

```python
"""
BybitCandleFeed — subscribes to Bybit public kline WebSocket topics and
delivers confirmed candle closes via asyncio.Queue.

Architecture:
- pybit's WebSocket runs in a background thread (threading model, not async)
- Confirmed candles are put() on a thread-safe asyncio.Queue via
  loop.call_soon_threadsafe to bridge into the async event loop
- Each symbol maintains a rolling in-memory window of N candles
  (the strategy's required_candle_count) so we never need REST for history
  after the initial warm-up

Warm-up:
- On start, fetch required_candle_count candles via REST to populate the window
- Only after window is full does the feed begin delivering snapshots
- This avoids the strategy receiving an incomplete snapshot on the first candle

Reconnection:
- pybit handles WebSocket reconnection internally
- On reconnect, a gap may exist in the candle history
- The feed detects gaps by checking if the new candle's open time is more than
  2 * interval_seconds ahead of the previous candle's open time
- On gap: re-fetch the window via REST and mark the snapshot as gap-recovered
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from backend.market_data.contracts import MarketCandle, MarketSnapshot


class CandleFeedSnapshot:
    """Delivered to consumers when a candle closes."""
    def __init__(self, snapshot: MarketSnapshot, gap_recovered: bool = False) -> None:
        self.snapshot = snapshot
        self.gap_recovered = gap_recovered


class BybitCandleFeed:
    def __init__(
        self,
        *,
        symbols: list[str],
        interval: str,                   # Bybit interval string: "1", "5", etc.
        window_size: int,                # = strategy.required_candle_count
        testnet: bool = False,
        rest_client: Any,                # BybitRESTClient for warm-up
        queue: asyncio.Queue[CandleFeedSnapshot] | None = None,
    ) -> None:
        self._symbols = symbols
        self._interval = interval
        self._interval_seconds = _interval_to_seconds(interval)
        self._window_size = window_size
        self._testnet = testnet
        self._rest = rest_client
        self._queue: asyncio.Queue[CandleFeedSnapshot] = queue or asyncio.Queue(maxsize=500)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any | None = None
        self._lock = threading.Lock()

        # Per-symbol rolling window: deque of MarketCandle (oldest → newest)
        self._windows: dict[str, deque[MarketCandle]] = {
            sym: deque(maxlen=window_size) for sym in symbols
        }
        self._warmed_up: dict[str, bool] = {sym: False for sym in symbols}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def queue(self) -> asyncio.Queue[CandleFeedSnapshot]:
        return self._queue

    async def start(self) -> None:
        """
        1. Grab the running event loop (must be called from async context).
        2. Warm up all symbol windows via REST.
        3. Start the WebSocket subscription.
        """
        self._loop = asyncio.get_running_loop()
        await self._warm_up_all()
        self._start_ws()
        logger.info(
            "BybitCandleFeed started. symbols={} interval={}",
            self._symbols,
            self._interval,
        )

    def stop(self) -> None:
        if self._ws is not None:
            try:
                self._ws.exit()
            except Exception:
                pass
            self._ws = None
        logger.info("BybitCandleFeed stopped.")

    # ------------------------------------------------------------------
    # Warm-up (REST)
    # ------------------------------------------------------------------

    async def _warm_up_all(self) -> None:
        for symbol in self._symbols:
            await self._warm_up_symbol(symbol)

    async def _warm_up_symbol(self, symbol: str) -> None:
        try:
            klines = await asyncio.to_thread(
                self._rest.get_klines,
                symbol=symbol,
                interval=self._interval,
                limit=self._window_size,
                category="spot",
            )
            ordered = sorted(klines, key=lambda k: k.start_time)
            window = self._windows[symbol]
            window.clear()
            for k in ordered:
                window.append(MarketCandle(
                    opened_at=k.start_time,
                    high=k.high_price,
                    low=k.low_price,
                    close=k.close_price,
                    volume=k.volume,
                ))
            self._warmed_up[symbol] = len(window) >= self._window_size
            logger.info(
                "Warm-up complete. symbol={} candles={} ready={}",
                symbol, len(window), self._warmed_up[symbol],
            )
        except Exception:
            logger.exception("Warm-up failed for {}.", symbol)

    # ------------------------------------------------------------------
    # WebSocket (runs in pybit background thread)
    # ------------------------------------------------------------------

    def _start_ws(self) -> None:
        try:
            from pybit.unified_trading import WebSocket  # type: ignore
        except ModuleNotFoundError:
            logger.error("pybit not installed — BybitCandleFeed cannot start WebSocket.")
            return

        self._ws = WebSocket(
            testnet=self._testnet,
            channel_type="public",
        )
        for symbol in self._symbols:
            topic = f"kline.{self._interval}.{symbol}"
            self._ws.kline_stream(
                interval=int(self._interval) if self._interval.isdigit() else self._interval,
                symbol=symbol,
                callback=self._on_kline_message,
            )
        logger.debug("WebSocket subscribed to kline topics.")

    def _on_kline_message(self, message: dict[str, Any]) -> None:
        """Called in pybit background thread. Must not block."""
        topic: str = message.get("topic", "")
        data_list: list[dict] = message.get("data", [])
        if not data_list:
            return

        # topic = "kline.1.BTCUSDT"
        parts = topic.split(".")
        if len(parts) < 3:
            return
        symbol = parts[2]

        for item in data_list:
            if not item.get("confirm", False):
                continue  # candle not yet closed — ignore in-progress updates
            self._handle_confirmed_candle(symbol, item)

    def _handle_confirmed_candle(self, symbol: str, item: dict[str, Any]) -> None:
        try:
            candle = MarketCandle(
                opened_at=datetime.fromtimestamp(int(item["start"]) / 1000, tz=UTC),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume", 0.0)),
            )
        except (KeyError, ValueError):
            logger.warning("Malformed kline item for {}: {}", symbol, item)
            return

        with self._lock:
            window = self._windows[symbol]

            # Gap detection
            gap_recovered = False
            if window and self._is_gap(window[-1], candle):
                logger.warning(
                    "Gap detected for {}. Last: {} New: {}. Re-warming.",
                    symbol, window[-1].opened_at, candle.opened_at,
                )
                # Re-warm in background — schedule on event loop
                if self._loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._warm_up_symbol(symbol), self._loop
                    )
                gap_recovered = True

            window.append(candle)
            if not self._warmed_up[symbol] and len(window) >= self._window_size:
                self._warmed_up[symbol] = True

            if not self._warmed_up[symbol]:
                return  # window still filling from REST warm-up

            snapshot = MarketSnapshot(
                symbol=symbol,
                interval=self._interval,
                candles=tuple(window),
            )

        feed_snapshot = CandleFeedSnapshot(snapshot=snapshot, gap_recovered=gap_recovered)
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, feed_snapshot
            )

    def _is_gap(self, last: MarketCandle, new: MarketCandle) -> bool:
        expected_next = last.opened_at + timedelta(seconds=self._interval_seconds)
        actual_next = new.opened_at
        return actual_next > expected_next + timedelta(seconds=self._interval_seconds * 1.5)


def _interval_to_seconds(interval: str) -> int:
    _MAP = {"1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
            "60": 3600, "120": 7200, "240": 14400, "D": 86400}
    if interval not in _MAP:
        raise ValueError(f"Unknown interval: {interval!r}")
    return _MAP[interval]
```

---

### 1.2 `backend/signal_loop/ws_runner.py`

Replaces the timer-based `SignalLoopRunner` for scalping intervals:

```python
"""
WebSocketSignalRunner — event-driven equivalent of SignalLoopRunner.

Instead of sleeping until the next candle close, this runner consumes
CandleFeedSnapshot objects from BybitCandleFeed.queue and fires the
strategy immediately on each confirmed close.

The queue acts as a natural buffer: if strategy evaluation + order
submission takes longer than one candle interval (unlikely but possible
on very fast strategies), candles are queued rather than dropped.

The queue maxsize=500 means at most 500 unprocessed candles can accumulate
before new ones are dropped — a safety valve against runaway backlogs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from backend.config import settings
from backend.market_data.bybit_ws_feed import BybitCandleFeed, CandleFeedSnapshot
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal
from backend.market_data.contracts import MarketSnapshot
from backend.signal_loop.runner import _SymbolState  # reuse cooldown logic


class WebSocketSignalRunner:
    def __init__(
        self,
        *,
        strategy: BaseStrategy[MarketSnapshot],
        execution_service,          # SupportsExecutionService protocol
        feed: BybitCandleFeed,
    ) -> None:
        self._strategy = strategy
        self._execution_service = execution_service
        self._feed = feed
        self._symbol_states: dict[str, _SymbolState] = {}
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        await self._feed.start()
        logger.info("WebSocketSignalRunner consuming feed queue.")
        while not self._stop_event.is_set():
            try:
                feed_snapshot = await asyncio.wait_for(
                    self._feed.queue.get(),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                continue  # check stop_event, then back to waiting

            await self._process_feed_snapshot(feed_snapshot)

    def stop(self) -> None:
        self._stop_event.set()
        self._feed.stop()

    async def _process_feed_snapshot(self, feed_snapshot: CandleFeedSnapshot) -> None:
        snapshot = feed_snapshot.snapshot
        symbol = snapshot.symbol

        state = self._symbol_states.setdefault(symbol, _SymbolState(symbol))
        if state.is_in_cooldown():
            return

        if feed_snapshot.gap_recovered:
            # After a gap, the window may have incomplete data — skip one candle
            logger.warning("Skipping signal after gap recovery for {}.", symbol)
            return

        try:
            signal = await self._strategy.generate_signal(snapshot)
        except Exception:
            logger.exception("Strategy error for {}.", symbol)
            return

        if signal is None:
            return

        logger.info(
            "WS signal. symbol={} direction={} entry={}",
            signal.symbol, signal.direction, signal.entry,
        )
        try:
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
            logger.warning("Safety guard: {}. Stopping.", exc)
            self.stop()
        except Exception:
            logger.exception("Execution error for {}.", symbol)
```

---

### 1.3 New config fields for scalping

```python
# In backend/config.py Settings:
scalping_enabled: bool = False
scalping_symbols: list[str] = []         # e.g. ["BTCUSDT", "ETHUSDT"]
scalping_interval: str = "1"             # "1" | "3" | "5"
scalping_ws_window_size: int = 50        # candle buffer depth (>= strategy.required_candle_count)
scalping_cooldown_seconds: int = 120     # per-symbol cooldown after entry
```

### 1.4 Lifespan wiring for scalping

```python
# In backend/main.py lifespan, AFTER existing day-trading loop section:
if settings.scalping_enabled:
    from backend.market_data.bybit_ws_feed import BybitCandleFeed
    from backend.signal_loop.ws_runner import WebSocketSignalRunner
    from backend.strategy_engine.ema_crossover import EMACrossoverStrategy  # swap to scalping strategy

    scalping_strategy = EMACrossoverStrategy(fast=5, slow=13, min_rrr=1.5)
    feed = BybitCandleFeed(
        symbols=settings.scalping_symbols,
        interval=settings.scalping_interval,
        window_size=settings.scalping_ws_window_size,
        testnet=settings.bybit_testnet,
        rest_client=app.state.rest_client,
    )
    ws_runner = WebSocketSignalRunner(
        strategy=scalping_strategy,
        execution_service=app.state.execution_service,
        feed=feed,
    )
    app.state.scalping_runner = ws_runner
    scalping_task = asyncio.create_task(ws_runner.run_forever())
    logger.info("WebSocket scalping runner started.")

    # in yield cleanup:
    ws_runner.stop()
    await asyncio.gather(scalping_task, return_exceptions=True)
```

---

## Phase 2 — Scalping Strategies

### Why EMA 9/21 fails on 1m–5m (quantified)

EMA crossover on 1m has known problems:
- A 9-period EMA on a 1m chart represents only 9 minutes of history
- In a ranging market (which 1m charts are ~70% of the time), the two EMAs
  cross back and forth constantly — this is pure noise
- Each false crossover burns 0.2% in fees (0.1% in + 0.1% out on Bybit spot taker)
- At RRR 2.0 with 40% win rate: expectancy = 0.4 × 2R - 0.6 × 1R = 0.2R per trade
  Before fees. At 0.2% fees per trade on a 1% risk trade: fees = 0.2% of equity.
  If average R = 1% equity, then fee is 20% of R. This wipes out a large chunk of edge.

### Strategy 1: VWAP Reversion (5m)

**Concept:** Price oscillates around VWAP (Volume Weighted Average Price) intraday.
When price deviates significantly from VWAP and starts reverting, fade the move.

**Implementation requirements:**
- VWAP resets at UTC midnight (intraday VWAP, not rolling)
- `MarketCandle.volume` is required (this is why Phase 2 of day trading roadmap must be done first)
- Minimum 30 candles to get a meaningful VWAP

**Signal logic:**
```
vwap = sum(typical_price * volume) / sum(volume)
     where typical_price = (high + low + close) / 3

deviation = (close - vwap) / vwap  # relative distance

LONG when:
  close < vwap * 0.998            # price at least 0.2% below VWAP
  AND previous close < current close  # price is bouncing up
  AND RSI(7) < 35                     # oversold confirmation
  entry = close
  stop  = close - 2 * atr(14)         # ATR-based stop (need ATR implementation)
  target = vwap                        # target is VWAP itself

SHORT when: mirror of above
```

**File:** `backend/strategy_engine/vwap_reversion_strategy.py`

**ATR calculation needed:**
```python
def _calculate_atr(highs: list[float], lows: list[float],
                   closes: list[float], period: int) -> list[float]:
    """Average True Range — measures volatility."""
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    # Simple average for first value, then RMA (Wilder's smoothing)
    atr = [sum(true_ranges[:period]) / period]
    for tr in true_ranges[period:]:
        atr.append((atr[-1] * (period - 1) + tr) / period)
    return atr
```

**required_candle_count:** 50 (VWAP needs several candles to stabilize; ATR needs 14+)

---

### Strategy 2: Bollinger Band Squeeze Breakout (5m)

**Concept:** When Bollinger Bands narrow (volatility compression), a breakout is
imminent. Trade the breakout direction with tight stop inside the squeeze.

**Signal logic:**
```
bb_upper, bb_middle, bb_lower = bollinger_bands(close, period=20, std=2.0)
band_width = (bb_upper - bb_lower) / bb_middle

squeeze = band_width < percentile_20(band_width, lookback=100)

LONG on breakout when:
  squeeze was True 2 candles ago
  AND close > bb_upper[-1]     # price broke above upper band
  AND close > close[-1]         # momentum confirmation
  entry = close
  stop  = bb_middle             # middle band as stop
  target = entry + 2 * (entry - stop)

SHORT: mirror
```

**File:** `backend/strategy_engine/bb_squeeze_strategy.py`

**required_candle_count:** 120 (20 for BB + 100 for band_width percentile history)

---

### Strategy 3: RSI Divergence (5m–15m)

**Concept:** Price makes a new low/high but RSI does not confirm it (divergence).
This signals exhaustion and a potential reversal.

**Signal logic:**
```
# Find last two swing lows (for bullish divergence / LONG setup):
swing_lows = [i for i in range(2, n-2) if closes[i] < closes[i-1]
              and closes[i] < closes[i+1]
              and closes[i] < closes[i-2]
              and closes[i] < closes[i+2]]

if len(swing_lows) >= 2:
    i1, i2 = swing_lows[-2], swing_lows[-1]
    price_made_lower_low = closes[i2] < closes[i1]
    rsi_made_higher_low  = rsi[i2] > rsi[i1]
    
    LONG when price_made_lower_low AND rsi_made_higher_low:
        entry = current close
        stop  = closes[i2] - small_buffer  # below the most recent swing low
        target = entry + 2 * (entry - stop)
```

**File:** `backend/strategy_engine/rsi_divergence_strategy.py`

**required_candle_count:** 60 (need enough history to find two swing lows)

---

### Choosing which strategy to run at what timeframe

| Timeframe | Recommended Strategy | Why |
|-----------|---------------------|-----|
| 1m | **Not recommended yet** | Too noisy without order book data; fees dominate |
| 3m | VWAP Reversion | Frequent mean-reversion setups; volume confirmation |
| 5m | BB Squeeze Breakout | Squeeze periods are common; clear invalidation point |
| 5m | RSI Divergence | Good precision; fewer false signals than EMA crossover |
| 15m | RSI+EMA Confluence (from day trading roadmap) | Trend + momentum confirmation |

**Start with 5m VWAP Reversion or BB Squeeze, not 1m.**
1m requires order book data (Phase 5 of this roadmap) to be profitable.

---

## Phase 3 — Risk Management for High-Frequency Trading

### 3.1 Config changes for scalping

| Setting | Scalping Value | Reasoning |
|---------|---------------|-----------|
| `risk_per_trade_pct` | 0.003 (0.3%) | More trades per day → smaller risk each |
| `min_rrr` | 1.5 | Scalping targets are tighter; 1.5 is realistic |
| `max_open_positions` | 2 | Allow 2 concurrent symbols |
| `max_total_risk_exposure_pct` | 0.01 (1%) | 0.3% × 2 + buffer |
| `max_daily_loss_pct` | 0.02 (2%) | Tighter — more trades means faster drawdown |
| `max_consecutive_losses` | 4 | Scalping has more losses; 3 may halt too early |
| `cooldown_hours` | 1 | 4h kills an entire scalping session |
| `hard_pause_consecutive_losses` | 6 | |
| `order_mode` | `taker_allowed` | **Critical** — scalping requires immediate fill |
| `max_trades_per_day` | 30 | ~1 trade per 30min per symbol at 5m interval |
| `scalping_cooldown_seconds` | 120 | 2 candles cooldown on 1m, 0.4 candles on 5m |

### 3.2 Fee awareness

Bybit spot taker fee: **0.1% per side**. Round trip = **0.2%**.

For a 0.3% risk trade at RRR 1.5:
- Win: +0.45% reward − 0.2% fees = +0.25% net
- Loss: −0.3% risk − 0.2% fees = −0.5% net
- Required win rate to break even: 0.5 / (0.25 + 0.5) = **66.7%**

This means scalping is only profitable if your strategy produces win rates above ~67%.
This is extremely hard for any EMA-based strategy.

**The math demands either:**
1. A genuinely high-precision strategy (VWAP reversion can hit 60-70% win rate)
2. Higher RRR (≥2.0, but this conflicts with scalping's nature)
3. Maker order fills (post-only = 0.1% rebate on Bybit spot maker = 0% effective fee)

**Recommendation:** Use `order_mode = "maker_preferred"` for scalping too.
Accept that some orders won't fill (maker rejected on fast moves), but the fee
advantage is essential for the math to work.

### 3.3 Per-symbol daily loss tracking

The current `CircuitBreaker` tracks total daily loss but not per-symbol.
For multi-symbol scalping, it's useful to blacklist a specific symbol that has
been losing repeatedly (e.g., ETHUSDT down 5 consecutive losses in 1 hour)
without stopping trading on BTCUSDT.

Add to `DailyStat` (requires Alembic migration):
```python
symbol_stats: Mapped[dict | None] = mapped_column(JSON, default=None)
# Format: {"BTCUSDT": {"losses": 3, "wins": 1}, "ETHUSDT": {"losses": 5, "wins": 0}}
```

Add to `SignalLoopRunner._evaluate_symbol()`:
```python
if self._symbol_stats.get(symbol, {}).get("consecutive_losses", 0) >= 5:
    logger.warning("Symbol {} blacklisted for today (5 consecutive losses).", symbol)
    return
```

---

## Phase 4 — Performance and DB Considerations

### 4.1 Database write volume

A day-trading bot at 15m makes ~4–8 trades per day. Each trade = ~10 DB writes
(Trade creation + 6-8 status transitions in TradeEvent).

A scalping bot at 5m on 2 symbols could make ~30–60 trades per day.
That's 300–600 DB writes. PostgreSQL handles this trivially.

No schema changes needed. But add a DB index if not present:
```sql
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol, status);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade_id ON trade_events(trade_id);
```

These are already in Alembic if the migrations were generated with proper metadata,
but verify with `\d trades` in psql.

### 4.2 `ExchangeSyncEngine` reconciliation interval

Currently `_DEFAULT_RECONCILIATION_INTERVAL_SECONDS = 5.0`.

For scalping, this is fine. The `start_reconciliation_worker()` polls every 5s
to catch fills that the WebSocket missed. At 1m candles, 5s is fast enough.

However, for true scalping speed, the private WebSocket (already running via
`BybitWebSocketListener`) handles order/execution events in near real-time.
The REST reconciliation is just a backup. No change needed here.

### 4.3 Connection pool under higher load

`backend/database.py` already has:
```python
pool_size=10, max_overflow=20, pool_timeout=30,
pool_recycle=1800, pool_pre_ping=True
```

For a scalping bot at 30 trades/day, concurrent DB connections peak at maybe 3–5
(signal loop + sync engine + API requests). The current pool is oversized. No change needed.

---

## Phase 5 — Order Book Data (True 1m Scalping, Optional)

This phase is only needed if you want to trade 1m timeframes. It requires significant
additional infrastructure.

### What order book data provides

On 1m charts, most strategies have 40–55% win rates. The edge you need to be
profitable comes from order book signals:

- **Bid/ask imbalance** — if there are 10× more buy orders than sell orders near
  current price, the next move is likely up
- **Large order walls** — a large limit order at a round number acts as support/resistance
- **Spoofing detection** — large orders that appear and disappear without filling

### Data source: Bybit WebSocket `orderbook.{depth}.{symbol}`

Bybit provides `orderbook.1`, `orderbook.50`, `orderbook.200` (depth levels).

Subscribe in `BybitCandleFeed` or a separate `OrderBookFeed`:
```python
self._ws.orderbook_stream(depth=50, symbol=symbol, callback=self._on_orderbook)
```

### Implementation sketch: `backend/market_data/orderbook.py`

```python
@dataclass
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    bids: list[tuple[float, float]]   # [(price, qty), ...] sorted desc
    asks: list[tuple[float, float]]   # [(price, qty), ...] sorted asc
    bid_volume: float                  # total bid qty in top N levels
    ask_volume: float                  # total ask qty in top N levels

    @property
    def imbalance(self) -> float:
        """Positive = more bids (bullish pressure), negative = more asks (bearish)."""
        total = self.bid_volume + self.ask_volume
        return (self.bid_volume - self.ask_volume) / total if total else 0.0
```

### Strategy using order book: `backend/strategy_engine/ob_momentum_strategy.py`

```
LONG when:
  imbalance > 0.3      (30% more buy volume than sell volume)
  AND close > ema_20   (trend filter)
  AND last 2 candles closed green
  entry = ask[0][0]    (best ask = immediate fill at taker)
  stop  = bid[0][0] - buffer
  target = entry + 2 * (entry - stop)
```

This strategy has the highest potential win rate of all described here (~65–75%)
but also the highest implementation complexity.

**Estimated effort for Phase 5:** 5–7 days, not including strategy testing.

---

## Phase 6 — Automated Strategy Validation Before Going Live

Before running any new strategy on real money, enforce a validation gate.

### `scripts/validate_strategy.py`

```
1. Run backtest on last 30 days of 1m/5m data (use SimulationExecutionService)
2. Check:
   - win_rate > 0.55
   - expectancy > 0 (accounting for fees)
   - max_drawdown < 10 * avg_risk_amount
   - closed_trades >= 30  (enough sample size)
3. If any check fails: print FAILED and exit 1 (blocks deployment)
4. If all pass: print PASSED and write validation_result.json
```

This script should be run in CI (GitHub Actions) whenever a strategy file changes.
A failed validation prevents the new strategy from being deployed.

---

## Full Scalping Execution Order

```
Prerequisite (from day-trading roadmap):
  ✅ Phase 1: Signal loop (runner.py)
  ✅ Phase 2: Volume field on MarketCandle
  ✅ Phase 4: Backtest (SimulationExecutionService)
  ✅ Phase 6: max_trades_per_day guard

Scalping Phase 1 (Week 3-4):
  Day 1-3:   BybitCandleFeed (ws_feed.py) with warm-up + gap detection
  Day 4-5:   WebSocketSignalRunner (ws_runner.py) + lifespan wiring
  Day 6-7:   Integration test: feed delivers snapshots for BTCUSDT on testnet

Scalping Phase 2 (Week 5-6):
  Day 1-3:   VWAP Reversion strategy (5m) + ATR calculation
  Day 4-5:   BB Squeeze strategy (5m)
  Day 6-7:   Backtest both strategies on 2000+ 5m candles each

Scalping Phase 3 (Week 7):
  Day 1-2:   Risk config for scalping + per-symbol loss tracking
  Day 3-5:   48h testnet run with scalping_enabled=true on BTCUSDT 5m

Scalping Phase 4 (Week 8):
  Day 1-2:   DB index audit
  Day 3-5:   Validate strategy (scripts/validate_strategy.py)
  Day 6-7:   Review 48h testnet metrics, adjust config

Scalping Phase 5 (Month 2, optional):
  Week 1-2:  OrderBookFeed + OrderBookSnapshot
  Week 3-4:  ob_momentum_strategy + backtest
```

---

## Go/No-Go Criteria for Scalping on Real Money

| Criterion | Threshold |
|-----------|-----------|
| Testnet win rate (last 100 trades) | ≥ 57% |
| Testnet expectancy (after simulated fees) | > 0.15R |
| Max drawdown in 48h testnet | < 5% equity |
| Zero stuck trades (all reach PNL_RECORDED) | Required |
| Circuit breaker test passes | Required |
| `validate_strategy.py` passes | Required |
| Scalping cooldown prevents double-entry | Required |

If any criterion fails: fix it first. Do not "test on small amounts" — a 0.3% risk
trade at $10K equity = $30 per trade. That's real money and a broken strategy at
30 trades/day = $900/day in losses.
