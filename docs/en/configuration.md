# Configuration Guide

This document explains all environment variables found in the `.env.example` / `.env` files. These settings control the bot's core behavior, connection parameters, risk boundaries, and safety layers.

---

## ⚙️ General Settings

| Variable | Type | Default | Description |
|---|---|---|---|
| `ENVIRONMENT` | `string` | `development` | Defines the execution environment. Can be `development` or `production`. |
| `DEBUG` | `bool` | `True` | Enables debug mode for FastAPI and more verbose stack traces. |
| `LOG_LEVEL` | `string` | `DEBUG` | Standard python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

---

## 🏦 Bybit API Credentials

| Variable | Type | Default | Description |
|---|---|---|---|
| `BYBIT_TESTNET` | `bool` | `True` | Forces the bot to connect to Bybit Testnet (`True`) or Bybit Mainnet (`False`). |
| `BYBIT_API_KEY` | `string` | `''` | Your Bybit API public key. Required if not in Shadow mode. |
| `BYBIT_API_SECRET` | `string` | `''` | Your Bybit API secret key. Keep this secure and never commit it! |

---

## 🗄️ Database Configuration

| Variable | Type | Example | Description |
|---|---|---|---|
| `DATABASE_URL` | `string` | `postgresql+asyncpg://...` | Connection DSN for your PostgreSQL instance. Must use `postgresql+asyncpg` for async processing via SQLAlchemy. |

---

## 📈 Trading Engine

| Variable | Type | Expected Values | Description |
|---|---|---|---|
| `TRADING_MODE` | `string` | `shadow`, `demo`, `real` | Controls execution flow. <br>• **shadow**: Calculates everything but places *no trades* on the exchange.<br>• **demo**: Executes in Bybit demo/testnet.<br>• **real**: Authorized to place live funds at risk. |
| `ORDER_MODE` | `string` | `maker_only`, `maker_preferred`, `taker_allowed` | Affects order request types.<br>• **maker_only**: Forces Post-Only. Order is rejected if it executes instantly.<br>• **maker_preferred**: Tries Post-Only first, falls back to market.<br>• **taker_allowed**: Allows instant market execution. |
| `SHADOW_EQUITY` | `float` | `10000.0` | Simulated account equity used in shadow mode for position sizing. |
| `USE_TRAILING_STOP` | `bool` | `False` | Determines if trailing safety stops should be pushed to the exchange. Intentionally disabled for the MVP. |
| `DEMO_CLOSE_TTL_SECONDS` | `int` | `30` | How long the demo runner waits after an entry reaches `POSITION_OPEN` before it submits a market close order. |
| `DEMO_POLL_INTERVAL_SECONDS` | `float` | `1.0` | Poll interval used by the demo runner while waiting for DB state transitions driven by WebSocket events. |
| `DEMO_TESTNET_SYMBOL` | `string` | `''` | Opt-in symbol for the live testnet e2e flow. |
| `DEMO_TESTNET_ENTRY` | `float` | `0.0` | Limit entry price used by the testnet e2e flow. |
| `DEMO_TESTNET_STOP` | `float` | `0.0` | Stop-loss price used by the testnet e2e flow. |
| `DEMO_TESTNET_TARGET` | `float` | `0.0` | Target price used by the testnet e2e flow. |

---

## 🔁 Autonomous Signal Loop

| Variable | Type | Default | Description |
|---|---|---|---|
| `SIGNAL_LOOP_ENABLED` | `bool` | `False` | Enables the REST-polled autonomous strategy loop for day-trading intervals. |
| `SIGNAL_LOOP_SYMBOLS` | `list[str]` | `[]` | Comma-separated symbols evaluated by the signal loop, for example `BTCUSDT,ETHUSDT`. |
| `SIGNAL_LOOP_INTERVAL` | `string` | `15` | Bybit kline interval used by the signal loop. |
| `SIGNAL_LOOP_COOLDOWN_SECONDS` | `int` | `300` | Per-symbol entry cooldown after the runner submits a signal. |
| `SIGNAL_LOOP_MAX_SYMBOLS_CONCURRENT` | `int` | `5` | Concurrency cap for parallel per-symbol strategy evaluations. |

## ⚡ Scalping

| Variable | Type | Default | Description |
|---|---|---|---|
| `SCALPING_ENABLED` | `bool` | `False` | Enables the public WebSocket candle feed plus the event-driven scalping runner. |
| `SCALPING_SYMBOLS` | `list[str]` | `[]` | Comma-separated symbols for the scalping runner. Do not overlap with `SIGNAL_LOOP_SYMBOLS`. |
| `SCALPING_INTERVAL` | `string` | `5` | Confirmed kline interval used by the scalping feed. The first supported production path is `5`. |
| `SCALPING_WS_WINDOW_SIZE` | `int` | `50` | Rolling candle buffer depth kept in memory per symbol. Must satisfy the active strategy candle requirement. |
| `SCALPING_COOLDOWN_SECONDS` | `int` | `120` | Per-symbol cooldown for the WebSocket scalping runner after a successful entry. |

---

## 💰 Risk Management

| Variable | Type | Default | Description |
|---|---|---|---|
| `RISK_PER_TRADE_PCT` | `float` | `0.01` (1%) | Percentage of total active equity you are willing to risk on a single trade (distance from Entry to Stop Loss). |
| `MIN_RRR` | `float` | `2.0` | Minimum Risk-to-Reward ratio allowed. Signals suggesting an RRR lower than this will be rejected automatically. |
| `MAX_OPEN_POSITIONS` | `int` | `1` | Strict cap on simultaneous open positions to avoid over-exposure. |
| `MAX_TOTAL_RISK_EXPOSURE_PCT` | `float` | `0.03` (3%) | The absolute maximum sum of all floating risks (`RISK_PER_TRADE_PCT` combined) allowed concurrently. |
| `MAX_TRADES_PER_DAY` | `int` | `10` | Hard daily entry cap enforced before order submission. This protects autonomous runners from runaway signal generation. |

Recommended scalping overrides:

- `ORDER_MODE=maker_preferred`
- `RISK_PER_TRADE_PCT=0.003`
- `MIN_RRR=1.5`
- `MAX_OPEN_POSITIONS=2`
- `MAX_TOTAL_RISK_EXPOSURE_PCT=0.01`
- `MAX_TRADES_PER_DAY=30`
- `MAX_DAILY_LOSS_PCT=0.02`
- `MAX_CONSECUTIVE_LOSSES=4`
- `HARD_PAUSE_CONSECUTIVE_LOSSES=6`
- `COOLDOWN_HOURS=1`

---

## 🛡️ Safety Guard (Circuit Breaker)

These settings are critical. They determine when the bot should forcibly stop accepting new signals to prevent equity destruction.

| Variable | Type | Default | Description |
|---|---|---|---|
| `MAX_DAILY_LOSS_PCT` | `float` | `0.03` (3%) | If the daily realized PnL drops below this threshold, all new signals are blocked. |
| `MAX_WEEKLY_LOSS_PCT` | `float` | `0.05` (5%) | Hard weekly circuit breaker. Requires manual intervention/reset if breached. |
| `MAX_CONSECUTIVE_LOSSES` | `int` | `3` | Cooldown threshold. Once today’s consecutive losing-trade counter reaches this value, new entries are paused until the cooldown window expires or an operator resets safety state. |
| `HARD_PAUSE_CONSECUTIVE_LOSSES` | `int` | `5` | Hard loss-streak threshold. Once today’s consecutive losing-trade counter reaches this value, the bot enters a manual-reset-required pause. |
| `COOLDOWN_HOURS` | `int` | `4` | How long the cooldown pause stays active after the cooldown threshold is hit. |

## Safety State Semantics

- `kill_switch_active=true` blocks all new entries until `POST /safety/reset` clears it.
- `pause_reason=daily_loss` or `pause_reason=weekly_loss` also require `POST /safety/reset`.
- `pause_reason=hard_loss_streak` requires `POST /safety/reset`.
- `pause_reason=cooldown` is cleared automatically after `cooldown_until`, and the runtime also clears the active consecutive-loss streak when that auto-expiry happens.

## Reset Behavior

- `POST /safety/reset` always clears `kill_switch_active`, `pause_reason`, `cooldown_until`, and `manual_reset_required`.
- Reseting a `cooldown` or `hard_loss_streak` pause also clears the currently tracked daily consecutive-loss counter.
- Resetting `daily_loss` or `weekly_loss` does not erase the underlying realized-loss statistics. If the same daily or weekly threshold is still breached, the next safety check pauses trading again immediately.
- The kill switch never auto-closes positions. It only blocks new entries and cancels pending entry orders in `demo` / `real` mode.

## Demo/Testnet Notes

- The default fast test suite does not hit Bybit.
- `pytest -m testnet` is opt-in and requires valid Bybit demo credentials plus the `DEMO_TESTNET_*` price fields.
- Market buy orders are standardized on base quantity via `marketUnit="baseCoin"`.
- `SCALPING_SYMBOLS` and `SIGNAL_LOOP_SYMBOLS` must not overlap. The app fails fast during lifespan startup if they do.
