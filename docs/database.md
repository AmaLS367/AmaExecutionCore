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
- lifecycle status
- realized PnL and hold time

Important status additions:

- `ORDER_PENDING_UNKNOWN`
- `POSITION_CLOSE_PENDING`
- `POSITION_CLOSE_FAILED`
- `PNL_RECORDED`

### `daily_stats`

Aggregates realized daily outcomes used by the circuit breaker:

- trade counts
- consecutive losses
- daily loss percentage
- circuit breaker flag

### `system_events`

Audit trail for kill switch, circuit breaker, cooldown, and operational errors.

### `safety_state`

Singleton-style table for global execution safety state. It persists bot stop conditions across process restarts.
