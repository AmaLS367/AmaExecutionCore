from __future__ import annotations

import gzip
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from backend.grid_engine.grid_advisor import suggest_grid
from backend.grid_engine.grid_backtester import RawCandle, run_grid_backtest
from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_metrics import GridBacktestResult, evaluate_grid_backtest

REGRESSION_DIR = Path("scripts/fixtures/regression")
TRAIN_30D_CANDLES = 30 * 24 * 4


def main() -> int:
    xrp_candles = _load_fixture(REGRESSION_DIR / "xrpusdt_15m_365d.json.gz")
    btc_candles = _load_fixture(REGRESSION_DIR / "btcusdt_15m_365d.json.gz")
    eth_candles = _load_fixture(REGRESSION_DIR / "ethusdt_15m_365d.json.gz")

    xrp_results = [
        _run_named_config("XRPUSDT Narrow", "XRPUSDT", xrp_candles, range_pct=0.10, n_levels=10),
        _run_named_config("XRPUSDT Medium", "XRPUSDT", xrp_candles, range_pct=0.20, n_levels=16),
        _run_named_config("XRPUSDT Wide", "XRPUSDT", xrp_candles, range_pct=0.30, n_levels=20),
    ]
    _run_named_config("BTCUSDT Medium", "BTCUSDT", btc_candles, range_pct=0.20, n_levels=16)
    _run_named_config("ETHUSDT Medium", "ETHUSDT", eth_candles, range_pct=0.20, n_levels=16)
    atr_results = [
        _run_atr_suggested_config("XRPUSDT ATR-suggested", "XRPUSDT", xrp_candles),
        _run_atr_suggested_config("BTCUSDT ATR-suggested", "BTCUSDT", btc_candles),
        _run_atr_suggested_config("ETHUSDT ATR-suggested", "ETHUSDT", eth_candles),
    ]

    xrp_gate_passed = any(
        result.net_pnl_usdt > 0 and result.fee_coverage_ratio >= 2.0 for result in xrp_results
    )
    atr_step_gate_passed = all(result.config_step_pct >= 0.005 for result in atr_results)
    atr_positive_symbols = sum(1 for result in atr_results if result.net_pnl_usdt > 0)
    return 0 if xrp_gate_passed and atr_step_gate_passed and atr_positive_symbols >= 2 else 1


def _run_named_config(
    label: str,
    symbol: str,
    candles: Sequence[RawCandle],
    *,
    range_pct: float,
    n_levels: int,
) -> GridBacktestResult:
    start_price = _candle_close(candles[0])
    config = GridConfig(
        symbol=symbol,
        p_min=start_price * (1 - range_pct),
        p_max=start_price * (1 + range_pct),
        n_levels=n_levels,
        capital_usdt=20.0,
    )
    result = _run_config(config, candles)
    print(f"[GRID CONFIG] {label}: p_min={config.p_min:.8f} p_max={config.p_max:.8f} "
          f"n_levels={config.n_levels} step_pct={config.step_pct:.4%}")
    result.print_report()
    return result


def _run_atr_suggested_config(
    label: str,
    symbol: str,
    candles: Sequence[RawCandle],
) -> GridBacktestResult:
    train_candles = candles[:TRAIN_30D_CANDLES]
    validation_candles = candles[TRAIN_30D_CANDLES:]
    config = suggest_grid(train_candles, capital_usdt=20.0, symbol=symbol)
    result = _run_config(config, validation_candles)
    print(f"[GRID CONFIG] {label}: p_min={config.p_min:.8f} p_max={config.p_max:.8f} "
          f"n_levels={config.n_levels} step_pct={config.step_pct:.4%}")
    result.print_report()
    return result


def _run_config(config: GridConfig, candles: Sequence[RawCandle]) -> GridBacktestResult:
    state = run_grid_backtest(config, candles)
    return evaluate_grid_backtest(state, config, backtest_days=_backtest_days(candles))


def _load_fixture(path: Path) -> list[RawCandle]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        loaded: object = json.load(file)
    candles = loaded.get("candles") if isinstance(loaded, Mapping) else loaded
    if not isinstance(candles, list):
        raise TypeError(f"Fixture {path} must contain a candle list.")
    return [_normalize_candle(candle) for candle in candles]


def _normalize_candle(candle: object) -> RawCandle:
    if isinstance(candle, Mapping):
        normalized: dict[str, object] = {}
        for key, value in candle.items():
            if not isinstance(key, str):
                raise TypeError(f"Candle key must be a string, got {key!r}.")
            normalized[key] = value
        return normalized
    if isinstance(candle, list):
        return list(candle)
    raise ValueError(f"Unsupported candle format: {candle!r}")


def _candle_close(candle: RawCandle) -> float:
    if isinstance(candle, Mapping):
        close = candle.get("close")
        if close is None:
            raise ValueError("Candle missing close.")
        return _to_float(close)
    return _to_float(candle[4])


def _backtest_days(candles: Sequence[RawCandle]) -> int:
    return max(1, round(len(candles) * 15 / 1_440))


def _to_float(raw_value: object) -> float:
    if isinstance(raw_value, bool):
        raise TypeError(f"Expected numeric value, got {raw_value!r}.")
    if isinstance(raw_value, int | float | str):
        return float(raw_value)
    raise ValueError(f"Expected numeric value, got {raw_value!r}.")


if __name__ == "__main__":
    sys.exit(main())
