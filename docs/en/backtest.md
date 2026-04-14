# Backtest And Replay Guide

`backend/backtest/` now contains three distinct runtime helpers with different purposes. They are not interchangeable.

## Historical Replay

Historical replay is implemented by `backend.backtest.HistoricalReplayRunner`.

Entry contract:

- `HistoricalReplayRequest.symbol`
- `HistoricalReplayRequest.interval`
- exactly one of:
  - `candles`: a historical candle sequence
  - `snapshots`: an explicit historical snapshot sequence
- optional `start_step`
- optional `end_step`

Step semantics:

- `end_step` is exclusive
- when replaying `candles`, the runner builds rolling snapshots using the active strategy's `required_candle_count`
- when replaying `snapshots`, the runner reuses the provided snapshots as-is

Replay result shape:

- `request`: normalized replay request
- `steps`: ordered replay steps
- `report`: machine-readable metrics derived from known execution outcomes

Each replay step contains:

- `step_index`
- `snapshot`
- `signal`
- `execution`

This keeps replay output usable from tests, scripts, or later automation without adding UI/report rendering.

## Metrics And Reporting

`HistoricalReplayResult.report` currently exposes:

- `metrics.closed_trades`
- `metrics.winning_trades`
- `metrics.losing_trades`
- `metrics.expectancy`
- `metrics.win_rate`
- `metrics.profit_factor`
- `metrics.max_drawdown`
- `slippage.count`
- `slippage.average`
- `slippage.minimum`
- `slippage.maximum`

Important limits:

- metrics are only computed from execution results that actually expose `realized_pnl`
- slippage summary is only computed when execution results expose `slippage`
- if replay execution does not provide those values, the report leaves the affected fields as `None`
- no synthetic fills, inferred PnL, or guessed analytics are added

## Strategy Usage

Replay works with any strategy that follows the existing `BaseStrategy` contract.

For multi-strategy runs, `backend.strategy_engine.StrategyOrchestrator` provides a minimal ordered composition layer:

- it evaluates strategies in order
- the first non-`None` signal wins
- snapshot requirements are widened to the largest `required_candle_count` in the set

This is intentionally narrow. It is not a general plugin framework or scheduling system.

## Replay vs Shadow vs Demo

Use the right helper for the right job:

- `HistoricalReplayRunner`: historical sequence playback for backtest-style evaluation
- `ShadowRunner`: one-shot runtime path from snapshot -> strategy -> execution service, typically with `TRADING_MODE=shadow`
- `DemoRunner`: opt-in Bybit testnet helper for validation against external services

Current boundaries:

- replay does not depend on live/demo wiring
- shadow mode is still an execution-core validation path, not a historical simulator
- demo mode is still a testnet helper, not a generalized backtest runner

## Current Limitations

The current replay implementation is intentionally narrow:

- no batch job scheduler
- no HTML or dashboard reporting
- no automatic historical data download
- no portfolio-level analytics beyond the reported aggregate metrics
- no claim that replay behavior matches exchange execution perfectly

Use replay to evaluate strategy behavior on a supplied historical sequence, not to infer production-grade execution guarantees.
