# API Guide

## Health

### `GET /health`

Returns the current runtime mode and a redacted credential status.

## Signal Execution

### `POST /signals/execute`

Primary entrypoint for the execution core.

Request body:

```json
{
  "symbol": "BTCUSDT",
  "direction": "long",
  "entry": 100.0,
  "stop": 90.0,
  "target": 130.0,
  "reason": "optional",
  "strategy_version": "optional",
  "indicators_snapshot": {"optional": "json"}
}
```

Response body:

```json
{
  "signal_id": "uuid",
  "trade_id": "uuid",
  "order_link_id": "string",
  "status": "order_submitted",
  "mode": "shadow",
  "replayed": false
}
```

Idempotency semantics:

- The public API deduplicates by a deterministic fingerprint of the normalized request body.
- Normalization rules:
  - `symbol` is trimmed and uppercased.
  - `direction` is matched by exact enum value.
  - `entry`, `stop`, and `target` are fingerprinted as normalized decimal strings.
  - `reason` and `strategy_version` are trimmed; blank values are treated as `null`.
  - `indicators_snapshot` is fingerprinted as canonical JSON with sorted keys.
- If an equivalent request is replayed while its linked trade is still non-terminal, the endpoint returns `200` with the same `signal_id` and `trade_id` plus `replayed=true`.
- If the linked trade is `order_pending_unknown`, replay triggers another exchange status check and still does not submit a second order.
- Once the linked trade reaches a terminal state, the same request body is allowed to create a fresh `Signal` and `Trade`.

Error semantics:

- `422`: risk validation or exposure limit failure
- `423`: kill switch, cooldown, or circuit-breaker pause is active

## Safety

### `POST /safety/kill`

Activates the persistent kill switch and cancels pending exchange orders in `demo` or `real`.

### `POST /safety/reset`

Clears persistent kill/pause state after operator review.

### `GET /safety/status`

Returns:

- `kill_switch`
- `pause_reason`
- `cooldown_until`
- `manual_reset_required`
- `trading_mode`
