# Database Guide

## Core Tables

### `signals`

Stores normalized strategy intent:

- symbol
- abstract direction
- reason
- strategy version
- indicator snapshot

### `trades`

Stores the full execution lifecycle:

- risk snapshot at entry
- entry order link / exchange order ID
- close order link / exchange order ID
- fill prices, fees, slippage
- current lifecycle status
- realized PnL and hold time

Important status additions:

- `ORDER_PENDING_UNKNOWN`
- `POSITION_CLOSE_PENDING`
- `POSITION_CLOSE_FAILED`
- `PNL_RECORDED`

### `trade_events`

Append-only audit trail for trade lifecycle changes.

Each row records:

- the owning `trade_id`
- an `event_type`
- `from_status`
- `to_status`
- optional metadata describing the source of the transition

The runtime currently writes:

- `trade_created` when a new `Trade` row is first persisted
- `status_transition` whenever the existing runtime moves a trade between lifecycle states

`trades.status` remains the mutable current-state snapshot. `trade_events` is the source of transition history for audit and reconstruction.

### `daily_stats`

Aggregates realized daily outcomes used by the circuit breaker:

- trade counts
- consecutive losses
- daily loss percentage
- circuit breaker flag

Runtime-maintained analytics fields:

- `gross_pnl`
- `total_fees`
- `net_pnl`

Runtime-deferred analytics fields:

- `starting_equity`
- `ending_equity`

These deferred fields remain in the schema but are not currently populated by runtime code.

Trade-level deferred analytics:

- `mae`
- `mfe`

### `system_events`

Audit trail for kill switch, circuit breaker, cooldown, and operational errors.

### `safety_state`

Singleton-style table for global execution safety state. It persists bot stop conditions across process restarts.
