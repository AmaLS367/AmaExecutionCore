# RSI EMA Spot V2 Validation

Date: 2026-04-28

## Scope

`rsi_ema_spot_v2` was validated as a separate long-only Spot strategy candidate. The legacy `rsi_ema` strategy remains unchanged for comparison.

The bounded sweep used:

- LTF EMA: `20`, `50`
- RSI thresholds: `40/60`, `38/62`, `35/65`
- HTF EMA: `20`, `50`
- HTF mode: `close_above_ema_only`, `close_above_ema_and_slope5`
- Max hold: `20`; `32` only for candidates with sufficient trade count
- Fixed constraints: long-only, `min_rrr=1.5`, `target_rrr=1.5`
- Fixtures: BTCUSDT, ETHUSDT, SOLUSDT, 365d 15m

Command:

```bash
python scripts/rsi_ema_sweep.py --quiet
```

## Result

No candidate passed all per-symbol thresholds:

- `closed_trades >= 15`
- `win_rate >= 0.51`
- `profit_factor >= 1.1`
- positive net expectancy

## Decision

Do not switch `scripts/fixtures/backtest_manifest.json` to a new `rsi_ema_spot_v2` regression profile yet. The strategy code and sweep tooling can remain available for research and live-shadow experiments, but CI should not be made green by weakening thresholds or selecting a sparse candidate.
