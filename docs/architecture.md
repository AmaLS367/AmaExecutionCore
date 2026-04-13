# Architecture

AmaExecutionCore keeps a strict pipeline:

`strategy -> execution service -> order executor -> exchange sync -> trade journal`

## Ownership

- `strategy_engine`: produces `StrategySignal` and never touches exchange or DB services.
- `signal_execution`: persists `Signal`, invokes `OrderExecutor`, and returns the current persisted trade snapshot.
- `order_executor`: performs risk checks, safety checks, exposure checks, idempotency, and order submission.
- `exchange_sync`: consumes private Bybit WebSocket events and advances trade state plus PnL.
- `safety_guard`: enforces persistent kill switch, daily/weekly pauses, and cooldown windows.
- `position_manager`: issues close orders for demo/manual exits.
- `backtest`: contains the `ShadowRunner` and `DemoRunner`.

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
- Close-order fills transition trades through `POSITION_CLOSED` to `PNL_RECORDED`.
- `DailyStat` is updated from realized close events, not only from pre-trade checks.
