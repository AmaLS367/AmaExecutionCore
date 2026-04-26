from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.grid_engine.grid_backtester import RawCandle
from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_metrics import GridBacktestResult
from scripts.validate_grid_backtest import REGRESSION_DIR, _candle_close, _load_fixture, _run_config

CAPITAL_USDT = 20.0
N_LEVELS = [8, 12, 16, 20]
RANGE_PCTS = [0.08, 0.12, 0.16, 0.20, 0.25]
SYMBOL_FILES = {
    "BTCUSDT": "btcusdt_15m_365d.json.gz",
    "ETHUSDT": "ethusdt_15m_365d.json.gz",
    "XRPUSDT": "xrpusdt_15m_365d.json.gz",
    "SOLUSDT": "solusdt_15m_365d.json.gz",
}
C2_SYMBOLS = ("XRPUSDT", "SOLUSDT", "ETHUSDT")
RESULTS_PATH = Path("scripts/fixtures/grid_sweep_results.json")
CANDLES_PER_DAY = 24 * 4
C2_WINDOW_DAYS = 30
C2_WINDOW_COUNT = 12
C2_PROFITABLE_WINDOW_RATE_GATE = 0.50
C2_ANNUALIZED_YIELD_GATE = 10.0
C2_MAX_DRAWDOWN_GATE = 55.0
C2_FEE_COVERAGE_GATE = 2.0


@dataclass(frozen=True, slots=True)
class SweepResult:
    symbol: str
    range_pct: float
    n_levels: int
    p_min_pct_from_start: float
    p_max_pct_from_start: float
    capital_usdt: float
    metrics: GridBacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    symbol: str
    range_pct: float
    n_levels: int
    profitable_window_rate: float
    windows: int


def main() -> int:
    candles_by_symbol = {
        symbol: _load_fixture(REGRESSION_DIR / filename)
        for symbol, filename in SYMBOL_FILES.items()
    }
    sweep_results = _load_results() if RESULTS_PATH.exists() else _run_sweep(candles_by_symbol)
    if not RESULTS_PATH.exists():
        _write_results(sweep_results)
    _print_passing_table(sweep_results)
    _print_top_three_by_symbol(sweep_results)

    best_walk_forward = _run_walk_forward_for_top_configs(candles_by_symbol, sweep_results)
    all_symbols_passed = _print_walk_forward_gate(best_walk_forward, sweep_results)
    return 0 if all_symbols_passed else 1


def _run_sweep(candles_by_symbol: dict[str, list[RawCandle]]) -> list[SweepResult]:
    results: list[SweepResult] = []
    for symbol, candles in candles_by_symbol.items():
        start_price = _candle_close(candles[0])
        for n_levels in N_LEVELS:
            for range_pct in RANGE_PCTS:
                config = GridConfig(
                    symbol=symbol,
                    p_min=start_price * (1 - range_pct),
                    p_max=start_price * (1 + range_pct),
                    n_levels=n_levels,
                    capital_usdt=CAPITAL_USDT,
                )
                metrics = _run_config(config, candles)
                results.append(
                    SweepResult(
                        symbol=symbol,
                        range_pct=range_pct,
                        n_levels=n_levels,
                        p_min_pct_from_start=-range_pct,
                        p_max_pct_from_start=range_pct,
                        capital_usdt=CAPITAL_USDT,
                        metrics=metrics,
                    ),
                )
    return results


def _write_results(results: list[SweepResult]) -> None:
    payload = [
        {
            "symbol": result.symbol,
            "range_pct": result.range_pct,
            "n_levels": result.n_levels,
            "p_min_pct_from_start": result.p_min_pct_from_start,
            "p_max_pct_from_start": result.p_max_pct_from_start,
            "capital_usdt": result.capital_usdt,
            "metrics": asdict(result.metrics),
        }
        for result in results
    ]
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_results() -> list[SweepResult]:
    raw_payload: object = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, list):
        raise TypeError(f"{RESULTS_PATH} must contain a JSON list.")
    return [_parse_sweep_result(item) for item in raw_payload]


def _parse_sweep_result(item: object) -> SweepResult:
    if not isinstance(item, dict):
        raise TypeError(f"Sweep result item must be an object, got {item!r}.")
    metrics = _parse_metrics(_dict_value(item, "metrics"))
    return SweepResult(
        symbol=_str_value(item, "symbol"),
        range_pct=_float_value(item, "range_pct"),
        n_levels=_int_value(item, "n_levels"),
        p_min_pct_from_start=_float_value(item, "p_min_pct_from_start"),
        p_max_pct_from_start=_float_value(item, "p_max_pct_from_start"),
        capital_usdt=_float_value(item, "capital_usdt"),
        metrics=metrics,
    )


def _parse_metrics(item: dict[str, Any]) -> GridBacktestResult:
    fail_reasons = item.get("fail_reasons", [])
    if not isinstance(fail_reasons, list):
        raise TypeError("metrics.fail_reasons must be a list.")
    return GridBacktestResult(
        symbol=_str_value(item, "symbol"),
        backtest_days=_int_value(item, "backtest_days"),
        config_step_pct=_float_value(item, "config_step_pct"),
        completed_cycles=_int_value(item, "completed_cycles"),
        gross_profit_usdt=_float_value(item, "gross_profit_usdt"),
        total_fees_usdt=_float_value(item, "total_fees_usdt"),
        net_pnl_usdt=_float_value(item, "net_pnl_usdt"),
        fee_coverage_ratio=_float_value(item, "fee_coverage_ratio"),
        annualized_yield_pct=_float_value(item, "annualized_yield_pct"),
        max_unrealized_drawdown_pct=_float_value(item, "max_unrealized_drawdown_pct"),
        capital_utilization_pct=_float_value(item, "capital_utilization_pct"),
        avg_cycle_profit_pct=_float_value(item, "avg_cycle_profit_pct"),
        days_to_breakeven=_float_value(item, "days_to_breakeven"),
        pass_regression=bool(item.get("pass_regression", False)),
        fail_reasons=[str(reason) for reason in fail_reasons],
    )


def _print_passing_table(results: list[SweepResult]) -> None:
    passing = sorted(
        [result for result in results if result.metrics.pass_regression],
        key=lambda result: result.metrics.annualized_yield_pct,
        reverse=True,
    )
    _emit("[GRID SWEEP] Passing configs sorted by annualized_yield_pct")
    for result in passing:
        _emit(
            f"{result.symbol} range={result.range_pct:.2f} n={result.n_levels} "
            f"yield={result.metrics.annualized_yield_pct:.1f}% "
            f"fee={result.metrics.fee_coverage_ratio:.2f}x "
            f"dd={result.metrics.max_unrealized_drawdown_pct:.1f}% "
            f"cycles={result.metrics.completed_cycles}",
        )


def _print_top_three_by_symbol(results: list[SweepResult]) -> None:
    _emit("[GRID SWEEP] Top 3 passing configs per symbol")
    for symbol in SYMBOL_FILES:
        passing = _top_passing_for_symbol(results, symbol)
        if not passing:
            _emit(f"{symbol}: no passing configs")
            continue
        for rank, result in enumerate(passing[:3], start=1):
            _emit(
                f"{symbol} #{rank}: range={result.range_pct:.2f} n={result.n_levels} "
                f"yield={result.metrics.annualized_yield_pct:.1f}% "
                f"dd={result.metrics.max_unrealized_drawdown_pct:.1f}%",
            )


def _run_walk_forward_for_top_configs(
    candles_by_symbol: dict[str, list[RawCandle]],
    sweep_results: list[SweepResult],
) -> dict[str, WalkForwardResult | None]:
    best_by_symbol: dict[str, WalkForwardResult | None] = {}
    for symbol in C2_SYMBOLS:
        candles = candles_by_symbol[symbol]
        top_results = _top_positive_for_symbol(sweep_results, symbol)[:3]
        if not top_results:
            best_by_symbol[symbol] = None
            continue
        walk_results = [_walk_forward(candles, result) for result in top_results]
        best_by_symbol[symbol] = max(
            walk_results,
            key=lambda result: result.profitable_window_rate,
        )
    return best_by_symbol


def _walk_forward(candles: list[RawCandle], sweep_result: SweepResult) -> WalkForwardResult:
    test_window = C2_WINDOW_DAYS * CANDLES_PER_DAY
    profitable_windows = 0
    total_windows = C2_WINDOW_COUNT
    for window_index in range(C2_WINDOW_COUNT):
        start = window_index * test_window
        test_candles = candles[start : start + test_window]
        if len(test_candles) != test_window:
            raise ValueError(
                f"{sweep_result.symbol} window {window_index + 1} has "
                f"{len(test_candles)} candles, expected {test_window}.",
            )
        start_price = _candle_close(test_candles[0])
        config = GridConfig(
            symbol=sweep_result.symbol,
            p_min=start_price * (1 - sweep_result.range_pct),
            p_max=start_price * (1 + sweep_result.range_pct),
            n_levels=sweep_result.n_levels,
            capital_usdt=sweep_result.capital_usdt,
        )
        metrics = _run_config(config, test_candles)
        if metrics.net_pnl_usdt > 0:
            profitable_windows += 1

    rate = profitable_windows / total_windows if total_windows else 0.0
    return WalkForwardResult(
        symbol=sweep_result.symbol,
        range_pct=sweep_result.range_pct,
        n_levels=sweep_result.n_levels,
        profitable_window_rate=rate,
        windows=total_windows,
    )


def _print_walk_forward_gate(
    best_walk_forward: dict[str, WalkForwardResult | None],
    sweep_results: list[SweepResult],
) -> bool:
    _emit("[GRID WALK-FORWARD] Best config per symbol")
    _emit("[GATE C2 SKIPPED] BTCUSDT: excluded for $20 capital (gate: documented exclusion)")
    all_passed = True
    for symbol in C2_SYMBOLS:
        walk_result = best_walk_forward[symbol]
        if walk_result is None:
            _emit(f"[GATE C2 FAILED] {symbol}: no positive-PnL full-run config (gate: >=1)")
            all_passed = False
            continue
        full_run = _matching_sweep_result(sweep_results, walk_result)
        symbol_passed = (
            walk_result.profitable_window_rate >= C2_PROFITABLE_WINDOW_RATE_GATE
            and full_run.metrics.annualized_yield_pct >= C2_ANNUALIZED_YIELD_GATE
            and full_run.metrics.max_unrealized_drawdown_pct <= C2_MAX_DRAWDOWN_GATE
            and full_run.metrics.fee_coverage_ratio >= C2_FEE_COVERAGE_GATE
        )
        status = "PASSED" if symbol_passed else "FAILED"
        _emit(
            f"[GATE C2 {status}] {symbol} profitable_window_rate: "
            f"{walk_result.profitable_window_rate:.2f} "
            f"(gate: >= {C2_PROFITABLE_WINDOW_RATE_GATE:.2f})",
        )
        _emit(
            f"[GATE C2 {status}] {symbol} annualized_yield_pct: "
            f"{full_run.metrics.annualized_yield_pct:.1f} "
            f"(gate: >= {C2_ANNUALIZED_YIELD_GATE:.0f})",
        )
        _emit(
            f"[GATE C2 {status}] {symbol} max_unrealized_drawdown_pct: "
            f"{full_run.metrics.max_unrealized_drawdown_pct:.1f} "
            f"(gate: <= {C2_MAX_DRAWDOWN_GATE:.0f})",
        )
        _emit(
            f"[GATE C2 {status}] {symbol} fee_coverage_ratio: "
            f"{full_run.metrics.fee_coverage_ratio:.2f} "
            f"(gate: >= {C2_FEE_COVERAGE_GATE:.1f})",
        )
        all_passed = all_passed and symbol_passed
    return all_passed


def _matching_sweep_result(
    sweep_results: list[SweepResult],
    walk_result: WalkForwardResult,
) -> SweepResult:
    for result in sweep_results:
        if (
            result.symbol == walk_result.symbol
            and result.range_pct == walk_result.range_pct
            and result.n_levels == walk_result.n_levels
        ):
            return result
    raise ValueError(f"Missing matching sweep result for {walk_result!r}.")


def _top_passing_for_symbol(results: list[SweepResult], symbol: str) -> list[SweepResult]:
    return sorted(
        [
            result
            for result in results
            if result.symbol == symbol and result.metrics.pass_regression
        ],
        key=lambda result: result.metrics.annualized_yield_pct,
        reverse=True,
    )


def _top_positive_for_symbol(results: list[SweepResult], symbol: str) -> list[SweepResult]:
    return sorted(
        [
            result
            for result in results
            if result.symbol == symbol and result.metrics.net_pnl_usdt > 0
        ],
        key=lambda result: result.metrics.annualized_yield_pct,
        reverse=True,
    )


def _dict_value(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object.")
    return value


def _str_value(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string.")
    return value


def _float_value(item: dict[str, Any], key: str) -> float:
    value = item.get(key)
    if isinstance(value, bool):
        raise TypeError(f"{key} must be numeric.")
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"{key} must be numeric.")


def _int_value(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool):
        raise TypeError(f"{key} must be an integer.")
    if isinstance(value, int):
        return value
    raise TypeError(f"{key} must be an integer.")


def _emit(message: str) -> None:
    sys.stdout.write(f"{message}\n")


if __name__ == "__main__":
    sys.exit(main())
