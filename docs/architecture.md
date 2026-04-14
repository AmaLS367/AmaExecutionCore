# Architecture

AmaExecutionCore targets the pipeline from `AGENTS.md`:

`market_data -> strategy_engine -> risk_manager -> safety_guard -> order_executor -> exchange_sync & trade_journal`

Today the live runtime entrypoint is the API layer:

`signal_execution -> order_executor -> exchange_sync -> trade_journal`

`position_manager` sits beside that entry flow. It does not open positions; it manages operator/demo close requests for trades that are already considered open on the exchange side.

## Ownership

- `strategy_engine`: produces `StrategySignal` and never touches exchange or DB services.
- `signal_execution`: persists `Signal`, invokes `OrderExecutor`, and returns the current persisted trade snapshot.
- `order_executor`: performs risk checks, safety checks, exposure checks, idempotency, and order submission.
- `exchange_sync`: owns exchange-order reconciliation after an order has an exchange identifier. It consumes private Bybit WebSocket events and periodically re-checks persisted entry/close order link IDs over REST.
- `safety_guard`: enforces persistent kill switch, daily/weekly pauses, and cooldown windows.
- `position_manager`: owns the runtime definition of "still open enough to manage" and owns close submission plus close retry after `POSITION_CLOSE_FAILED`.
- `backtest`: contains the `ShadowRunner` and `DemoRunner`.

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

- Open-order timeouts move trades into `ORDER_PENDING_UNKNOWN`.
- Entry and close orders use separate link IDs.
- The reconciliation worker runs during app lifespan for non-shadow modes.
- If a WebSocket event is missed, delayed, or arrives out of order, the worker re-checks persisted non-terminal trades through REST and converges the existing trade state when the exchange side becomes knowable.
- If REST still cannot resolve the exchange-side state, the trade remains in its current non-terminal status and is eligible for the next reconciliation pass, including after process restart.
- Close-order fills transition trades through `POSITION_CLOSED` to `PNL_RECORDED`.
- `DailyStat` is updated from realized close events, not only from pre-trade checks.
