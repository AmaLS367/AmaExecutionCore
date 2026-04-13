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
  "mode": "shadow"
}
```

Error semantics:

- `409`: duplicate non-terminal trade for the same signal
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
