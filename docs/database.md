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

## Supported Test Matrix

The repository intentionally keeps two DB test layers with different goals.

### Fast SQLite tests

Default tests use the in-memory `sqlite+aiosqlite` harness from `tests/conftest.py` and create tables through `Base.metadata.create_all`.

What they validate:

- service and router behavior
- ORM mappings used by the current runtime
- fast feedback for execution, safety, reconciliation, and backtest logic

What they do not validate:

- Alembic migration correctness
- PostgreSQL-specific column types, constraints, and DDL behavior
- drift between the runtime models and the migrated production schema

### PostgreSQL + Alembic integration tests

`tests/postgresql/` is the opt-in integration path for the real persistence stack. It requires `TEST_POSTGRESQL_URL` and runs `alembic upgrade head` before executing tests.

What they validate:

- schema creation through migrations rather than `Base.metadata.create_all`
- compatibility of the current ORM/runtime code with the migrated PostgreSQL schema
- critical persistence and API flows against PostgreSQL tables and constraints

Current coverage includes:

- trade-journal persistence for `Signal`, `Trade`, `SafetyState`, `DailyStat`, and `trade_events`
- `POST /signals/execute` replay behavior on migrated schema
- safety endpoints against persisted PostgreSQL state
- a basic shadow trade close lifecycle against the migrated schema

Limits:

- these tests are not a full end-to-end production simulation
- they do not prove exchange-side behavior, networking, or Bybit reconciliation correctness
- the target database must be disposable; the opt-in fixture resets the `public` schema before migrations
