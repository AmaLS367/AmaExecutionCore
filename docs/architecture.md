# Architecture

AmaExecutionCore is organized around the pipeline defined in `AGENTS.md`:

`market_data -> strategy_engine -> risk_manager -> safety_guard -> order_executor -> exchange_sync & trade_journal`

That full pipeline is now partially implemented through two concrete entry flows.

## Current Runtime Entry Flows

### API execution flow

`signal_execution -> order_executor -> exchange_sync -> trade_journal`

This path is used when an external caller already has a fully formed trade idea and submits it to `POST /signals/execute`.

### Shadow pipeline flow

`market_data -> strategy_engine -> risk_manager -> safety_guard -> order_executor -> trade_journal`

The current shadow pipeline is an on-demand entrypoint implemented by `backend/backtest/shadow_runner.py`:

1. `market_data`
   `BybitSpotSnapshotProvider` fetches recent spot klines over Bybit REST and normalizes them into a minimal `MarketSnapshot` made of ordered candles with `high`, `low`, and `close`.
2. `strategy_engine`
   `StrategyExecutionService` receives an explicit `symbol` and `interval`, normalizes them at the service boundary, requests the candle window required by the strategy, and runs the strategy against the normalized snapshot.
3. `risk_manager -> safety_guard -> order_executor`
   `OrderExecutor` still owns the pre-submit execution gate: position sizing, minimum RRR validation, open-position limit checks, total exposure checks, kill switch checks, and circuit-breaker checks.
4. `trade_journal`
   In `shadow` mode the trade is persisted with an execution record, but order placement stops before any exchange REST order submission.

This shadow flow is not a historical replay engine and it is not a scheduler or daemon. It runs once per explicit call with a caller-provided `symbol` and `interval`.

`position_manager` remains adjacent to these entry flows. It does not open positions; it manages operator/demo close requests for trades that are already considered open on the exchange side.

## Ownership

- `market_data`: owns upstream market snapshot retrieval and normalization. The current implementation is a Bybit Spot REST-backed snapshot provider for the first strategy.
- `strategy_engine`: produces `StrategySignal` from normalized snapshots and never touches exchange or DB persistence directly.
- `signal_execution`: persists `Signal`, invokes `OrderExecutor`, and returns the current persisted trade snapshot.
- `risk_manager`: currently contributes risk math and execution-side validation used by `OrderExecutor`.
- `safety_guard`: enforces persistent kill switch, daily/weekly pauses, and cooldown windows.
- `order_executor`: performs risk checks, safety checks, exposure checks, idempotency, and order submission or shadow persistence.
- `exchange_sync`: owns exchange-order reconciliation after an order has an exchange identifier. It consumes private Bybit WebSocket events and periodically re-checks persisted entry/close order link IDs over REST.
- `position_manager`: owns the runtime definition of "still open enough to manage" and owns close submission plus close retry after `POSITION_CLOSE_FAILED`.
- `trade_journal`: stores `Signal`, `Trade`, daily stats, system events, and persistent safety state.
- `backtest`: currently contains the reusable `ShadowRunner` entrypoint and the separate `DemoRunner` testnet helper.

## Strategy Snapshot Contract

The first strategy is `EMACrossoverStrategy`.

Its current expectations are intentionally minimal:

- `symbol`
- `interval`
- an ordered candle window with `high`, `low`, and `close`
- at least `slow + 1` candles so the EMA crossover can compare the last two EMA points

No historical replay abstraction, ticker stream, or broader market-universe orchestration has been added yet.

### Open-position truth

The durable record is always the persisted `Trade` row in the database.

`position_manager` owns the interpretation of which statuses still represent live exchange exposure for operator-facing flows:

- `POSITION_OPEN`
- `ORDER_PARTIALLY_FILLED`
- `POSITION_CLOSE_PENDING`
- `POSITION_CLOSE_FAILED`

That ownership is used by `/positions/open` and by close retry rules. `exchange_sync` updates those statuses from exchange-side evidence, but it does not decide which of them should stay visible/manageable as open positions.

### Recovery ownership

- Entry-order reconciliation belongs to `exchange_sync`.
  It re-checks trades in `ORDER_SUBMITTED` and `ORDER_PENDING_UNKNOWN` using the persisted `order_link_id`.
- Close-order reconciliation belongs to `exchange_sync`.
  It re-checks trades in `POSITION_CLOSE_PENDING` using the persisted `close_order_link_id`.
- Close-failure recovery belongs to `position_manager`.
  When a close order is rejected or cancelled, `exchange_sync` marks the trade as `POSITION_CLOSE_FAILED`; a later manual/demo close request from `position_manager` creates the replacement close order.
- Neither `exchange_sync` nor the reconciliation worker creates new trades.
  Recovery only mutates the existing `Trade` row that already owns the persisted exchange identifiers.

## Persistent Safety Model

`safety_state` is the single source of truth for:

- `kill_switch_active`
- `pause_reason`
- `cooldown_until`
- `manual_reset_required`
- `last_triggered_at`

The in-memory `KillSwitch` object mirrors persisted state but does not replace it.

## Lifecycle Notes

- Shadow mode persists a `Trade` record but does not call exchange order placement endpoints.
- The current shadow path therefore stops before `exchange_sync`; there is no exchange-side order to reconcile.
- Open-order timeouts move trades into `ORDER_PENDING_UNKNOWN`.
- Entry and close orders use separate link IDs.
- The reconciliation worker runs during app lifespan for non-shadow modes.
- If a WebSocket event is missed, delayed, or arrives out of order, the worker re-checks persisted non-terminal trades through REST and converges the existing trade state when the exchange side becomes knowable.
- If REST still cannot resolve the exchange-side state, the trade remains in its current non-terminal status and is eligible for the next reconciliation pass, including after process restart.
- Close-order fills transition trades through `POSITION_CLOSED` to `PNL_RECORDED`.
- `DailyStat` is updated from realized close events, not only from pre-trade checks.
