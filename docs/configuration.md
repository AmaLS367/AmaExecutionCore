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
| `USE_TRAILING_STOP` | `bool` | `False` | Determines if trailing safety stops should be pushed to the exchange. Intentionally disabled for the MVP. |
| `DEMO_CLOSE_TTL_SECONDS` | `int` | `30` | How long the demo runner waits after an entry reaches `POSITION_OPEN` before it submits a market close order. |
| `DEMO_POLL_INTERVAL_SECONDS` | `float` | `1.0` | Poll interval used by the demo runner while waiting for DB state transitions driven by WebSocket events. |
| `DEMO_TESTNET_SYMBOL` | `string` | `''` | Opt-in symbol for the live testnet e2e flow. |
| `DEMO_TESTNET_ENTRY` | `float` | `0.0` | Limit entry price used by the testnet e2e flow. |
| `DEMO_TESTNET_STOP` | `float` | `0.0` | Stop-loss price used by the testnet e2e flow. |
| `DEMO_TESTNET_TARGET` | `float` | `0.0` | Target price used by the testnet e2e flow. |

---

## 💰 Risk Management

| Variable | Type | Default | Description |
|---|---|---|---|
| `RISK_PER_TRADE_PCT` | `float` | `0.01` (1%) | Percentage of total active equity you are willing to risk on a single trade (distance from Entry to Stop Loss). |
| `MIN_RRR` | `float` | `2.0` | Minimum Risk-to-Reward ratio allowed. Signals suggesting an RRR lower than this will be rejected automatically. |
| `MAX_OPEN_POSITIONS` | `int` | `1` | Strict cap on simultaneous open positions to avoid over-exposure. |
| `MAX_TOTAL_RISK_EXPOSURE_PCT` | `float` | `0.03` (3%) | The absolute maximum sum of all floating risks (`RISK_PER_TRADE_PCT` combined) allowed concurrently. |

---

## 🛡️ Safety Guard (Circuit Breaker)

These settings are critical. They determine when the bot should forcibly stop accepting new signals to prevent equity destruction.

| Variable | Type | Default | Description |
|---|---|---|---|
| `MAX_DAILY_LOSS_PCT` | `float` | `0.03` (3%) | If the daily realized PnL drops below this threshold, all new signals are blocked. |
| `MAX_WEEKLY_LOSS_PCT` | `float` | `0.05` (5%) | Hard weekly circuit breaker. Requires manual intervention/reset if breached. |
| `MAX_CONSECUTIVE_LOSSES` | `int` | `3` | Number of sequential Stop-Loss hits permitted before taking a pause. |
| `COOLDOWN_HOURS` | `int` | `4` | How many hours the bot remains paused internally after stringing together `MAX_CONSECUTIVE_LOSSES`. |

## Safety State Semantics

- `kill_switch_active=true` blocks all new entries until `POST /safety/reset` clears it.
- `pause_reason=daily_loss` or `pause_reason=weekly_loss` also require `POST /safety/reset`.
- `pause_reason=cooldown` is cleared automatically after `cooldown_until`.

## Demo/Testnet Notes

- The default fast test suite does not hit Bybit.
- `pytest -m testnet` is opt-in and requires valid Bybit demo credentials plus the `DEMO_TESTNET_*` price fields.
- Market buy orders are standardized on base quantity via `marketUnit="baseCoin"`.
