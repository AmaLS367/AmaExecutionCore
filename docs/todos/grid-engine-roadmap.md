# AmaExecutionCore — Grid Engine Roadmap for Codex

## Context & Ground Rules

This document is the authoritative implementation plan for the Grid Trading Engine.
Execute stages in order. Do NOT skip validation gates.
After each stage, print a structured validation report. If any gate FAILS, STOP and report
the actual value, expected value, and which file/line caused the failure. Do not proceed
to the next stage when a gate fails.

### Repository layout (do not restructure)
- `backend/` — all business logic
- `backend/backtest/` — existing replay runner and simulation service (directional strategies — do not touch)
- `backend/strategy_engine/` — existing directional strategies (do not touch)
- `backend/grid_engine/` — NEW: all grid trading code lives here
- `scripts/` — runner scripts
- `scripts/fixtures/` — historical OHLCV data
- `tests/` — pytest tests
- `docs/todos/` — this file

### Before starting any stage
1. Run `uv run pytest -q --tb=short` — confirm all existing tests still pass.
2. Run `uv run ruff check .` — confirm no lint errors.
3. Run `uv run mypy .` — confirm no type errors.
4. Confirm you are on branch `dev`.
5. Print: `[GATE PRE-STAGE] All pre-checks passed.`

### Existing historical datasets available
All files are in `scripts/fixtures/regression/`:
- `btcusdt_15m_365d.json.gz` — 365 days, 15m candles
- `ethusdt_15m_365d.json.gz` — 365 days, 15m candles
- `solusdt_15m_365d.json.gz` — 365 days, 15m candles
- `xrpusdt_15m_365d.json.gz` — 365 days, 15m candles

Format per candle: `[open_time_ms, open, high, low, close, volume]`.
Use these files for all backtesting. Do NOT fetch live data.

---

## Grid Trading — Core Concepts

### How the grid works
1. Define a price range `[P_min, P_max]` split into `N` equal levels (arithmetic grid).
2. At each level `i`, one buy limit order sits at price `L_i` and one sell limit order sits at `L_i + step`.
3. When a buy at `L_i` fills → immediately place sell at `L_i + step`.
4. When a sell at `L_i + step` fills → immediately place buy at `L_i`.
5. Each completed buy→sell cycle earns `step - fees` per unit of base asset.

### Fee model (Bybit Spot non-VIP)
- Maker fee (limit order resting in book): **0.10%** per trade
- Taker fee (limit order crossing spread on placement): **0.10%** per trade
- Grid limit orders are assumed to rest (maker) → round-trip fee = **0.20%**
- Minimum profitable step: `step > 0.20%`. Target: `step ≥ 0.50%` for healthy margin.

### Fill simulation rule
In backtesting, a buy limit at price `P` is considered filled in candle `c` if `low_c ≤ P`.
A sell limit at price `P` is considered filled in candle `c` if `high_c ≥ P`.
Only one fill event per order per candle (the order fills at the limit price, not market price).

### Capital allocation
- Total capital `C` is split across `N` buy-side slots.
- Capital per slot: `C / N` in quote currency (USDT).
- Units per slot: `(C / N) / L_i` in base asset (rounded down to exchange minimum lot size).
- Sell-side inventory is acquired as buy orders fill over time.

---

## Grid Metrics (Honest Evaluation)

These replace win-rate and profit-factor. Grid trading is evaluated by:

### Primary metrics
| Metric | Formula | Gate (regression) | Gate (live candidate) |
|--------|---------|-------------------|-----------------------|
| **Net PnL** | `gross_grid_profit - total_fees` | > 0 | > 0 |
| **Fee Coverage Ratio** | `gross_grid_profit / total_fees` | ≥ 2.0 | ≥ 1.5 |
| **Annualized Grid Yield** | `(net_PnL / deployed_capital) × (365 / backtest_days)` | ≥ 15% | ≥ 10% |
| **Max Unrealized Drawdown** | `max(unrealized_loss_at_any_point) / deployed_capital` | ≤ 40% | ≤ 30% |
| **Completed Cycles** | Count of fully closed buy→sell pairs | ≥ 100 | ≥ 50 |

### Secondary metrics (reported but not gated)
- `avg_cycle_profit_pct` — average profit per completed cycle as % of position size
- `capital_utilization_pct` — % of candles where ≥ 1 buy order was active
- `days_to_breakeven` — days of grid profit to recover max unrealized drawdown
- `grid_efficiency` — `completed_cycles / total_possible_cycles` (where possible = price crossed a level)

### What "completed cycle" means
A cycle is one matched buy+sell pair at the same grid level. A buy order that never gets a matching
sell (because price dropped below P_min and stayed there) is an open/unrealized position — it counts
toward unrealized drawdown, NOT as a completed cycle.

---

## STAGE A — Grid Backtester

Build the core simulation engine. No live trading yet.

### A1 — Data model

Create `backend/grid_engine/__init__.py` (empty).

Create `backend/grid_engine/grid_config.py`:
```python
from dataclasses import dataclass, field

@dataclass
class GridConfig:
    symbol: str
    p_min: float          # lower bound of the grid
    p_max: float          # upper bound of the grid
    n_levels: int         # number of grid intervals (buy orders = n_levels)
    capital_usdt: float   # total USDT allocated to this grid
    maker_fee_pct: float = 0.001   # 0.10%
    min_lot_size: float = 0.0      # exchange minimum base asset per order (0 = no minimum)

    @property
    def step(self) -> float:
        return (self.p_max - self.p_min) / self.n_levels

    @property
    def step_pct(self) -> float:
        return self.step / self.p_min

    @property
    def capital_per_level(self) -> float:
        return self.capital_usdt / self.n_levels

    def buy_prices(self) -> list[float]:
        return [self.p_min + i * self.step for i in range(self.n_levels)]

    def sell_price(self, buy_price: float) -> float:
        return buy_price + self.step
```

Create `backend/grid_engine/grid_state.py`:
```python
from dataclasses import dataclass, field
from enum import Enum

class SlotStatus(Enum):
    WAITING_BUY = "waiting_buy"
    HOLDING = "holding"           # buy filled, waiting to place sell
    WAITING_SELL = "waiting_sell"

@dataclass
class GridSlot:
    level: int
    buy_price: float
    sell_price: float
    units: float                  # base asset per order (computed at init)
    status: SlotStatus = SlotStatus.WAITING_BUY
    buy_fill_price: float = 0.0
    sell_fill_price: float = 0.0
    completed_cycles: int = 0
    realized_pnl_usdt: float = 0.0

@dataclass
class GridState:
    config: "GridConfig"
    slots: list[GridSlot] = field(default_factory=list)
    total_fees_paid: float = 0.0
    total_gross_profit: float = 0.0
    unrealized_inventory_usdt: float = 0.0  # cost basis of unfilled buys still held
    candle_snapshots: list[dict] = field(default_factory=list)  # for drawdown tracking

    @property
    def net_pnl(self) -> float:
        return self.total_gross_profit - self.total_fees_paid

    @property
    def completed_cycles(self) -> int:
        return sum(s.completed_cycles for s in self.slots)
```

**Gate A1:**
- `GridConfig(symbol="XRPUSDT", p_min=1.80, p_max=2.20, n_levels=10, capital_usdt=20.0)`
  - `step` == 0.04 ✓
  - `step_pct` ≈ 0.0222 (2.22%) ✓ (must be > 0.005)
  - `len(buy_prices())` == 10 ✓
  - `sell_price(1.80)` == 1.84 ✓
  - `capital_per_level` == 2.0 ✓
- Import `GridConfig`, `GridState`, `GridSlot`, `SlotStatus` in a test — no errors.

### A2 — Grid Backtester engine

Create `backend/grid_engine/grid_backtester.py`.

The backtester takes a `GridConfig` and a list of candles `[open_time_ms, open, high, low, close, volume]`
and simulates the grid running over the full period.

Algorithm per candle:
1. For each slot in `WAITING_BUY` status: if `candle.low ≤ slot.buy_price` → fill the buy.
   - Deduct fee: `slot.units × slot.buy_price × maker_fee_pct`
   - Set slot status to `WAITING_SELL`.
2. For each slot in `WAITING_SELL` status: if `candle.high ≥ slot.sell_price` → fill the sell.
   - Compute gross profit: `slot.units × (slot.sell_price - slot.buy_price)`
   - Deduct fee: `slot.units × slot.sell_price × maker_fee_pct`
   - Increment `slot.completed_cycles`, add to `state.total_gross_profit`, set status to `WAITING_BUY`.
3. After processing fills: snapshot unrealized inventory value at `candle.close` for drawdown tracking.
   - Unrealized inventory = sum of `(slot.buy_price × slot.units)` for all `WAITING_SELL` slots.
   - Unrealized loss = `unrealized_inventory_at_cost - unrealized_inventory_at_close_price`.

Important: within a single candle, process ALL buy fills before sell fills (price moves low→high intracandle).

Initialization:
- Compute `slot.units = floor(config.capital_per_level / slot.buy_price / min_lot_size) × min_lot_size`
  (if min_lot_size == 0, use `config.capital_per_level / slot.buy_price` directly).
- All slots start as `WAITING_BUY`.
- Pre-fill slots where `buy_price ≥ starting_price` as `WAITING_SELL` with inventory purchased at start
  (the grid needs sell-side inventory for levels already above current price). For these pre-filled slots,
  deduct the initial buy cost and fee from capital tracking.

Create `backend/grid_engine/grid_metrics.py`:
```python
from dataclasses import dataclass

@dataclass
class GridBacktestResult:
    symbol: str
    backtest_days: int
    config_step_pct: float
    completed_cycles: int
    gross_profit_usdt: float
    total_fees_usdt: float
    net_pnl_usdt: float
    fee_coverage_ratio: float
    annualized_yield_pct: float
    max_unrealized_drawdown_pct: float
    capital_utilization_pct: float
    avg_cycle_profit_pct: float
    days_to_breakeven: float
    pass_regression: bool
    fail_reasons: list[str]

    def print_report(self) -> None:
        status = "PASS" if self.pass_regression else "FAIL"
        print(f"[GRID BACKTEST {status}] {self.symbol}")
        print(f"  Completed cycles:     {self.completed_cycles}")
        print(f"  Gross profit:         ${self.gross_profit_usdt:.4f}")
        print(f"  Total fees:           ${self.total_fees_usdt:.4f}")
        print(f"  Net PnL:              ${self.net_pnl_usdt:.4f}")
        print(f"  Fee coverage ratio:   {self.fee_coverage_ratio:.2f}x  (gate: ≥2.0)")
        print(f"  Annualized yield:     {self.annualized_yield_pct:.1f}%  (gate: ≥15%)")
        print(f"  Max unrealized DD:    {self.max_unrealized_drawdown_pct:.1f}%  (gate: ≤40%)")
        print(f"  Capital utilization:  {self.capital_utilization_pct:.1f}%")
        print(f"  Avg cycle profit:     {self.avg_cycle_profit_pct:.3f}%")
        print(f"  Days to breakeven:    {self.days_to_breakeven:.1f}d")
        if self.fail_reasons:
            for r in self.fail_reasons:
                print(f"  [FAIL] {r}")
```

Implement `evaluate_grid_backtest(state: GridState, config: GridConfig, backtest_days: int) -> GridBacktestResult`
that computes all metrics and checks gates.

**Gate A2:**
Run a synthetic test: generate 500 candles oscillating between 1.90 and 2.10 (sawtooth pattern).
Grid: XRPUSDT, P_min=1.80, P_max=2.20, N=10, capital=20.0.
- `completed_cycles ≥ 20` (price crossed 10 levels ~50 times = ≥20 full cycles expected)
- `net_pnl > 0`
- `fee_coverage_ratio ≥ 1.5`

### A3 — Backtester validation script

Create `scripts/validate_grid_backtest.py`. It must:
1. Load `scripts/fixtures/regression/xrpusdt_15m_365d.json.gz`.
2. Run three grid configurations:
   - **Narrow:** P_min = start_price × 0.90, P_max = start_price × 1.10, N=10
   - **Medium:** P_min = start_price × 0.80, P_max = start_price × 1.20, N=16
   - **Wide:**   P_min = start_price × 0.70, P_max = start_price × 1.30, N=20
3. Print `GridBacktestResult.print_report()` for each.
4. Also run medium config on BTCUSDT and ETHUSDT datasets.
5. Exit code 0 only if ≥ 1 config passes regression gate on XRPUSDT.

**Gate A3:**
Run `uv run python scripts/validate_grid_backtest.py`.
- Must exit 0.
- At least one config on XRPUSDT: `net_pnl > 0` AND `fee_coverage_ratio ≥ 2.0`.
- Print report must show all 5 runs (3 XRP + 1 BTC + 1 ETH).

---

## STAGE B — ATR-Based Grid Suggestion

Add a helper that recommends grid parameters based on recent volatility. This is used
by the FastAPI `/grid/suggest` endpoint (Stage D) and can be run standalone.

### B1 — ATR calculator

Create `backend/grid_engine/grid_advisor.py`:

```python
def suggest_grid(
    candles: list[list],        # recent candles [open_time_ms, o, h, l, c, v]
    capital_usdt: float,
    min_step_pct: float = 0.005,  # minimum 0.5% step
    target_n_levels: int = 10,
    atr_period: int = 20,
    atr_multiplier: float = 2.0,
) -> GridConfig:
    ...
```

Algorithm:
1. Compute ATR over last `atr_period` candles.
2. `half_range = atr_multiplier × ATR`
3. `P_min = current_price - half_range`, `P_max = current_price + half_range`
4. `step = (P_max - P_min) / target_n_levels`
5. If `step / P_min < min_step_pct`: increase `target_n_levels` down (fewer levels) until step_pct ≥ min_step_pct.
6. Return `GridConfig` with computed values.

**Gate B1:**
Unit test: feed 50 candles of XRPUSDT data, call `suggest_grid(candles, capital_usdt=20.0)`.
- `config.step_pct ≥ 0.005`
- `config.p_min > 0`
- `config.p_max > config.p_min`
- `config.n_levels ≥ 4`

### B2 — Validate suggested configs

Extend `scripts/validate_grid_backtest.py` to add a 4th run for each symbol:
- **ATR-suggested:** call `suggest_grid` using the first 30 days of candles as "recent" data,
  then run the backtest on the remaining 335 days.

Print the ATR-suggested config parameters alongside the result.

**Gate B2:**
- ATR-suggested config step_pct ≥ 0.5% for all 3 symbols.
- At least 2 of 3 symbols: net_pnl > 0 on the 335-day validation window.

---

## STAGE C — Parameter Sweep (Find Best Config)

Systematically find which grid parameters work best on 365-day data.

### C1 — Sweep script

Create `scripts/grid_parameter_sweep.py`. Parameters to sweep:
- `n_levels`: [8, 12, 16, 20]
- `range_pct` (half-width relative to starting price): [0.08, 0.12, 0.16, 0.20, 0.25]
- Symbols: BTCUSDT, ETHUSDT, XRPUSDT, SOLUSDT
- Capital: 20.0 USDT for all

Total: 4 × 5 × 4 = 80 combinations.

For each combination, run the backtest and record all metrics. Output:
1. A sorted table (by annualized_yield_pct descending) of all passing configs.
2. A summary of the top 3 configs per symbol.
3. Save results as `scripts/fixtures/grid_sweep_results.json`.

### C2 — Walk-forward validation of top configs

For each symbol, take the top-3 passing configs from C1 and do walk-forward validation:
- Window: 90-day train (use to calculate ATR-based initial price, no fitting), 30-day test
- Step: 30 days forward
- Run the same fixed config on each 30-day window
- Report: % of windows where net_pnl > 0 ("profitable window rate")

**Gate C2:**
Best config per symbol must have:
- `profitable_window_rate ≥ 0.60` (profitable in ≥ 60% of 30-day windows)
- `annualized_yield_pct ≥ 15%` on full 365-day run
- `max_unrealized_drawdown_pct ≤ 40%`
- `fee_coverage_ratio ≥ 2.0`

If no config passes for a symbol, report it as FAIL for that symbol. Bot will not trade that symbol live.

### C3 — Manifest update

Update `scripts/fixtures/backtest_manifest.json`:
- Add a new top-level key `"grid_profiles"` with the validated configs per symbol.
- Each entry: `{ symbol, p_min_pct_from_start, p_max_pct_from_start, n_levels, capital_usdt,
  walk_forward_profitable_window_rate, annualized_yield_pct, max_unrealized_drawdown_pct }`.
- Old directional strategy entries are kept as-is (do not remove).

---

## STAGE D — FastAPI Grid Control Endpoints

Add REST endpoints for managing the grid. No live trading yet — just config and state management.

### D1 — DB schema

Create `backend/grid_engine/models.py` with SQLAlchemy models (use existing DB session pattern):

```
GridSession: id, symbol, config_json, status (active/paused/stopped), created_at, stopped_at
GridSlotRecord: id, session_id, level, buy_price, sell_price, status, completed_cycles, realized_pnl
```

Create Alembic migration for these two tables.

**Gate D1:**
- `uv run alembic upgrade head` applies without error.
- `uv run alembic downgrade -1` rolls back without error.
- Re-apply: `uv run alembic upgrade head`.

### D2 — Grid router

Create `backend/api/grid_router.py` with these endpoints:

```
POST /grid/suggest
  Body: { symbol, capital_usdt, lookback_days? }
  Response: { p_min, p_max, n_levels, step_pct, estimated_annual_yield_pct }
  (Uses GridAdvisor on fetched or stored recent candles — use stored fixtures in dev mode)

POST /grid/create
  Body: { symbol, p_min, p_max, n_levels, capital_usdt }
  Response: { session_id, slots: [...], step_pct, warning_if_step_too_small }
  (Saves GridSession + GridSlotRecords to DB, status=paused)

GET /grid/{session_id}/status
  Response: { session_id, symbol, status, completed_cycles, net_pnl_usdt,
              fee_coverage_ratio, max_unrealized_drawdown_pct, slots: [...] }

POST /grid/{session_id}/start   (sets status=active — live trading Stage E)
POST /grid/{session_id}/pause   (sets status=paused)
POST /grid/{session_id}/stop    (sets status=stopped, cancels all open orders in Stage E)
```

Register router in main FastAPI app.

**Gate D2:**
- `POST /grid/suggest` with valid body → 200, returns `step_pct ≥ 0.005`.
- `POST /grid/create` → 200, returns session_id and correct number of slots.
- `GET /grid/{session_id}/status` → 200, `completed_cycles == 0`, `status == "paused"`.
- `POST /grid/{session_id}/pause` → 200.
- All existing API tests still pass.

---

## STAGE E — Live Grid Execution Engine

Connect the grid engine to Bybit via WebSocket for real order management.

### E1 — Order placement module

Create `backend/grid_engine/order_manager.py`.

Uses existing Bybit connector (pybit V5). Implements:
```python
class GridOrderManager:
    def place_buy_limit(self, symbol: str, price: float, qty: float) -> str  # returns order_id
    def place_sell_limit(self, symbol: str, price: float, qty: float) -> str
    def cancel_order(self, symbol: str, order_id: str) -> bool
    def cancel_all_orders(self, symbol: str) -> int  # returns count cancelled
    def get_open_orders(self, symbol: str) -> list[dict]
```

All calls use existing pybit V5 session. Log every order placement and cancellation.

**Gate E1:**
Unit test with mocked pybit session:
- `place_buy_limit` called with correct params → returns fake order_id.
- `cancel_all_orders` calls cancel for each open order.
- No real API calls in this test.

### E2 — WebSocket event handler

Create `backend/grid_engine/grid_ws_handler.py`.

Subscribes to Bybit private WebSocket `order` topic. On each `order_fill` event:
1. Look up which `GridSlotRecord` this order belongs to (by order_id stored in DB).
2. If buy filled → call `place_sell_limit` for this slot, update slot status in DB.
3. If sell filled → call `place_buy_limit` for this slot, update slot status in DB, increment completed_cycles.
4. Update `GridSession` PnL fields.

Handle reconnection: on WebSocket disconnect, reconcile local DB state against `get_open_orders()`
and repost any missing orders.

**Gate E2:**
Integration test (mocked WebSocket + mocked OrderManager):
- Fire a fake buy-fill event → verify `place_sell_limit` called with correct price.
- Fire a fake sell-fill event → verify `place_buy_limit` called with correct price, `completed_cycles == 1`.
- Fire disconnect → verify reconciliation calls `get_open_orders`.

### E3 — Grid runner service

Create `backend/grid_engine/grid_runner.py`:
```python
class GridRunner:
    async def start(self, session_id: int) -> None
    async def stop(self, session_id: int) -> None
    async def pause(self, session_id: int) -> None
```

`start()`:
1. Load `GridSession` from DB.
2. Compute `units` per slot from current price (fetched via REST, one call).
3. Place all `WAITING_BUY` orders via `GridOrderManager`.
4. Pre-fill sell-side slots for levels above current price.
5. Start WebSocket handler.

`stop()`:
1. Stop WebSocket handler.
2. Cancel all open orders via `cancel_all_orders`.
3. Update session status to `stopped`.

**Gate E3:**
End-to-end test with testnet credentials (if available) OR fully mocked:
- `start()` → verify N buy orders placed.
- Simulate fill of lowest buy order → verify sell placed at correct price.
- `stop()` → verify `cancel_all_orders` called.

### E4 — Wire start/stop endpoints to live runner

Update `POST /grid/{session_id}/start` to call `GridRunner.start()`.
Update `POST /grid/{session_id}/stop` to call `GridRunner.stop()`.

---

## STAGE F — Risk Management

### F1 — Bag holding circuit breaker

In `GridRunner`, after each candle tick (polled every 60s via REST `/v5/market/kline`):
1. Compute current unrealized drawdown = `sum(slot.buy_price × slot.units for WAITING_SELL slots)`
   minus current market value.
2. If `unrealized_drawdown_pct > config_max_dd_pct` (default 35%): call `pause()` and log alert.
3. If price < `P_min × 0.95` (5% below lower bound): call `stop()` and log alert.

### F2 — P_max breakout handler

If all slots are in `WAITING_BUY` (price ran above P_max, all sells filled):
- Set session status to `waiting_reentry`.
- Do not place any orders.
- Log the total profit from the breakout run.
- Notify via log: "Grid exhausted upside. All capital in USDT. Consider re-creating grid."

### F3 — Daily PnL report

Every 24h, log to DB and print:
- Completed cycles in last 24h
- Net PnL in last 24h
- Current unrealized position value
- Fee coverage ratio since session start

**Gate F (all):**
- Unit tests for circuit breaker: mock a session where unrealized DD = 36% → verify `pause()` called.
- Unit tests for P_max breakout: all slots WAITING_BUY → verify `waiting_reentry` status set.
- `uv run pytest -q --tb=short` still passes all tests.

---

## Appendix A — Grid Metric Reference

### Why not win rate / profit factor?
These metrics are designed for directional signal strategies. In grid trading:
- Every completed cycle is profitable by construction (sell price > buy price always).
- "Win rate" on completed trades would be ~100% — meaningless.
- Profit factor doesn't capture the bag-holding risk (the real danger).

### What actually kills a grid bot
1. **Trending market:** Price moves directionally below P_min and stays there. The bot accumulates
   inventory at ever-higher average cost basis while market keeps falling.
2. **Too-narrow step:** Step ≤ round-trip fees → each cycle loses money.
3. **Too-wide grid with low capital:** Each slot gets so little capital that minimum lot size
   can't be met → some levels never fill properly.

### The edge of grid trading
Grid trading does NOT need to predict direction. It profits from **realized volatility** (oscillations).
As long as the market oscillates within [P_min, P_max], it makes money regardless of trend within range.
The longer a market stays in range and the more it oscillates, the more cycles complete.

### Realistic annual yield expectations
- Step 1%, N=10, 365 days: if price oscillates 2× per day across 3 levels on average →
  `2 × 3 × 0.8% net per cycle × 365 = ~17.5%` annualized on deployed capital.
- These are optimistic estimates. Actual yield depends heavily on ranging vs trending behavior.

### Minimum capital per symbol (Bybit Spot minimums as of 2025)
| Symbol | Min order size | Min capital for 10-level grid |
|--------|---------------|-------------------------------|
| XRPUSDT | 1 XRP ≈ $2.00 | $20 (tight but viable) |
| SOLUSDT | 0.01 SOL ≈ $1.50 | $15 |
| ETHUSDT | 0.001 ETH ≈ $3.50 | $35 |
| BTCUSDT | 0.0001 BTC ≈ $10 | $100 |

For $20 capital: **XRPUSDT** or **SOLUSDT** are the only viable options.

---

## Appendix B — What Was Tried Before (Directional Strategies)

All directional strategies were abandoned after consistent failure across 730 days / 8 quarters.
Results summary:

| Strategy | Days tested | Win Rate | Profit Factor | Result |
|----------|-------------|----------|---------------|--------|
| VWAPReversionV1 (original) | 365 | ~40% | ~0.6 | FAIL |
| VWAPReversionV2 (tuned) | 365 | 40% | 0.46 | FAIL |
| EMAPullbackStrategy | 365 | 22.8% | <0.3 | FAIL |
| BreakoutStrategy (20-bar) | 365 | 27% | 0.18 | FAIL |
| TSMomentumStrategy + RelativeStrengthOrchestrator | 730 | 36-43% | 0.35-0.62 | FAIL |

Root cause: Simple technical signals on 15m crypto spot are close to random. Realistic fees
(0.2% round-trip) require a consistent edge that directional strategies cannot deliver.
Grid trading removes the need for directional prediction entirely.
