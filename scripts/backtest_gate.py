from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from loguru import logger

from backend.backtest import evaluate_scenario, load_manifest, serialize_evaluation
from backend.backtest.datasets import (
    SupportsKlineFetch,
    candles_for_lookback,
    fetch_candles_with_retry,
    load_dataset,
)
from backend.bybit_client.rest import BybitRESTClient
from backend.grid_engine.grid_backtester import RawCandle, run_grid_backtest
from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_metrics import GridBacktestResult, evaluate_grid_backtest
from scripts.validate_grid_backtest import _backtest_days, _candle_close, _load_fixture

GateMode = Literal["regression", "live"]


class BacktestGateError(RuntimeError):
    """Raised when one or more manifest scenarios fail their profile thresholds."""


@dataclass(slots=True, frozen=True)
class GridThresholdProfile:
    name: str
    min_completed_cycles: int
    min_profitable_window_rate: float | None
    min_annualized_yield_pct: float
    min_fee_coverage_ratio: float
    min_net_pnl_usdt: float
    max_unrealized_drawdown_pct: float


@dataclass(slots=True, frozen=True)
class GridBacktestScenario:
    name: str
    symbol: str
    dataset_path: str
    p_min_pct_from_start: float
    p_max_pct_from_start: float
    n_levels: int
    capital_usdt: float
    profile: str
    max_candles: int | None
    walk_forward_days: int | None
    walk_forward_windows: int | None


@dataclass(slots=True, frozen=True)
class GridScenarioEvaluation:
    name: str
    symbol: str
    profile: str
    metrics: GridBacktestResult
    profitable_window_rate: float | None
    passed: bool
    failure_reasons: tuple[str, ...]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run manifest-driven backtest gates.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("scripts/fixtures/backtest_manifest.json"),
    )
    parser.add_argument("--mode", choices=("regression", "live"), required=True)
    parser.add_argument(
        "--suite",
        help="Optional manifest suite name. If omitted, default_suites[mode] is used when present.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fee-rate-per-side", type=float, default=0.001)
    return parser


def _resolve_output_path(*, output: Path | None, mode: GateMode) -> Path:
    if output is not None:
        return output
    return Path("artifacts") / f"backtest-{mode}.json"


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper())


def _resolve_repo_root(manifest_path: Path) -> Path:
    return manifest_path.resolve().parents[2]


def _resolve_dataset_path(
    *,
    manifest_path: Path,
    repo_root: Path,
    dataset_path: str,
    for_write: bool = False,
) -> Path:
    raw_path = Path(dataset_path)
    if raw_path.is_absolute():
        return raw_path

    repo_candidate = (repo_root / raw_path).resolve()
    manifest_candidate = (manifest_path.parent / raw_path).resolve()
    if for_write:
        return repo_candidate if raw_path.parts and raw_path.parts[0] == "scripts" else manifest_candidate
    if repo_candidate.exists():
        return repo_candidate
    if manifest_candidate.exists():
        return manifest_candidate
    return repo_candidate if raw_path.parts and raw_path.parts[0] == "scripts" else manifest_candidate


async def run_manifest_gate(
    *,
    manifest_path: Path,
    mode: GateMode,
    output_path: Path,
    fee_rate_per_side: float,
    suite: str | None = None,
    client: SupportsKlineFetch | None = None,
) -> dict[str, object]:
    raw_manifest = _load_raw_manifest(manifest_path)
    selected_names = _selected_names_for_suite(raw_manifest, suite=suite, mode=mode)
    manifest = load_manifest(manifest_path)
    repo_root = _resolve_repo_root(manifest_path)
    results: list[dict[str, object]] = []
    live_client = client
    if mode == "live" and live_client is None:
        live_client = BybitRESTClient()

    for scenario in manifest.scenarios:
        if selected_names is not None and scenario.name not in selected_names:
            continue
        if mode == "regression":
            dataset = load_dataset(
                _resolve_dataset_path(
                    manifest_path=manifest_path,
                    repo_root=repo_root,
                    dataset_path=scenario.dataset_path,
                ),
            )
            candles = dataset.candles
            lookback_days = scenario.lookback_days
            profile = manifest.profiles[scenario.regression_profile]
        else:
            assert live_client is not None
            lookback_days = scenario.live_lookback_days
            candles = await fetch_candles_with_retry(
                live_client,
                symbol=scenario.symbol,
                interval=scenario.interval,
                lookback_days=lookback_days,
                retries=3,
                base_delay_seconds=2.0,
            )
            expected_candle_count = candles_for_lookback(interval=scenario.interval, lookback_days=lookback_days)
            if len(candles) < expected_candle_count:
                raise RuntimeError(
                    f"Fetched only {len(candles)} candles for {scenario.symbol} {scenario.interval}, expected {expected_candle_count}.",
                )
            profile = manifest.profiles[scenario.live_profile]

        evaluation = await evaluate_scenario(
            scenario=scenario,
            candles=candles,
            profile=profile,
            fee_rate_per_side=fee_rate_per_side,
            lookback_days=lookback_days,
        )
        serialized = serialize_evaluation(evaluation)
        results.append(serialized)
        status = "PASS" if evaluation.passed else "FAIL"
        print(
            f"[{status}] {scenario.name} symbol={scenario.symbol} interval={scenario.interval} "
            f"win_rate={serialized['win_rate']} profit_factor={serialized['profit_factor']} "
            f"max_drawdown_pct={serialized['max_drawdown_pct']}",
        )

    grid_profiles = _load_grid_profiles(raw_manifest)
    for grid_scenario in _load_grid_scenarios(raw_manifest):
        if selected_names is not None and grid_scenario.name not in selected_names:
            continue
        if grid_scenario.profile not in grid_profiles:
            raise ValueError(f"Unknown grid profile: {grid_scenario.profile}")
        grid_evaluation = _evaluate_grid_scenario(
            manifest_path=manifest_path,
            repo_root=repo_root,
            scenario=grid_scenario,
            profile=grid_profiles[grid_scenario.profile],
        )
        serialized = _serialize_grid_evaluation(grid_evaluation)
        results.append(serialized)
        status = "PASS" if grid_evaluation.passed else "FAIL"
        print(
            f"[{status}] {grid_scenario.name} engine=grid symbol={grid_scenario.symbol} "
            f"yield={grid_evaluation.metrics.annualized_yield_pct:.3f} "
            f"fee_coverage={grid_evaluation.metrics.fee_coverage_ratio:.3f} "
            f"max_unrealized_drawdown_pct={grid_evaluation.metrics.max_unrealized_drawdown_pct:.3f} "
            f"profitable_window_rate={grid_evaluation.profitable_window_rate}",
        )

    _validate_selected_names(selected_names=selected_names, results=results)
    report: dict[str, object] = {
        "mode": mode,
        "suite": suite,
        "manifest": str(manifest_path.as_posix()),
        "generated_at": datetime.now(UTC).isoformat(),
        "fee_rate_per_side": str(fee_rate_per_side),
        "all_passed": all(bool(item["passed"]) for item in results),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["all_passed"]:
        raise BacktestGateError(f"Backtest gate failed. See {output_path.as_posix()} for details.")
    return report


def _load_raw_manifest(path: Path) -> dict[str, object]:
    raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise TypeError("Backtest manifest must be a JSON object.")
    return {str(key): value for key, value in raw_payload.items()}


def _selected_names_for_suite(
    raw_manifest: Mapping[str, object],
    *,
    suite: str | None,
    mode: GateMode,
) -> set[str] | None:
    resolved_suite = suite
    if resolved_suite is None:
        default_suites = raw_manifest.get("default_suites")
        if isinstance(default_suites, Mapping):
            raw_default = default_suites.get(mode)
            if isinstance(raw_default, str):
                resolved_suite = raw_default
    if resolved_suite is None:
        return None

    suites = raw_manifest.get("suites")
    if not isinstance(suites, Mapping):
        raise TypeError("Manifest suite requested, but manifest does not define suites.")
    raw_names = suites.get(resolved_suite)
    if not isinstance(raw_names, list) or not raw_names:
        raise ValueError(f"Manifest suite {resolved_suite!r} must define at least one scenario.")
    selected: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str) or not raw_name:
            raise TypeError(f"Manifest suite {resolved_suite!r} contains invalid scenario name.")
        selected.add(raw_name)
    return selected


def _load_grid_profiles(raw_manifest: Mapping[str, object]) -> dict[str, GridThresholdProfile]:
    raw_profiles = raw_manifest.get("grid_threshold_profiles", {})
    if not isinstance(raw_profiles, Mapping):
        raise TypeError("grid_threshold_profiles must be an object.")
    profiles: dict[str, GridThresholdProfile] = {}
    for raw_name, raw_profile in raw_profiles.items():
        if not isinstance(raw_name, str):
            raise TypeError("Grid profile name must be a string.")
        profile_payload = _mapping_payload(raw_profile, f"grid_threshold_profiles.{raw_name}")
        profiles[raw_name] = GridThresholdProfile(
            name=raw_name,
            min_completed_cycles=_int_value(profile_payload, "min_completed_cycles", default=1),
            min_profitable_window_rate=_optional_float_value(
                profile_payload,
                "min_profitable_window_rate",
            ),
            min_annualized_yield_pct=_float_value(
                profile_payload,
                "min_annualized_yield_pct",
                default=0.0,
            ),
            min_fee_coverage_ratio=_float_value(
                profile_payload,
                "min_fee_coverage_ratio",
                default=0.0,
            ),
            min_net_pnl_usdt=_float_value(profile_payload, "min_net_pnl_usdt", default=0.0),
            max_unrealized_drawdown_pct=_float_value(
                profile_payload,
                "max_unrealized_drawdown_pct",
                default=100.0,
            ),
        )
    return profiles


def _load_grid_scenarios(raw_manifest: Mapping[str, object]) -> tuple[GridBacktestScenario, ...]:
    raw_scenarios = raw_manifest.get("grid_scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise TypeError("grid_scenarios must be a list.")
    scenarios: list[GridBacktestScenario] = []
    for index, raw_scenario in enumerate(raw_scenarios):
        payload = _mapping_payload(raw_scenario, f"grid_scenarios[{index}]")
        scenarios.append(
            GridBacktestScenario(
                name=_str_value(payload, "name"),
                symbol=_str_value(payload, "symbol").upper(),
                dataset_path=_str_value(payload, "dataset_path"),
                p_min_pct_from_start=_float_value(payload, "p_min_pct_from_start"),
                p_max_pct_from_start=_float_value(payload, "p_max_pct_from_start"),
                n_levels=_int_value(payload, "n_levels"),
                capital_usdt=_float_value(payload, "capital_usdt"),
                profile=_str_value(payload, "profile"),
                max_candles=_optional_int_value(payload, "max_candles"),
                walk_forward_days=_optional_int_value(payload, "walk_forward_days"),
                walk_forward_windows=_optional_int_value(payload, "walk_forward_windows"),
            ),
        )
    return tuple(scenarios)


def _evaluate_grid_scenario(
    *,
    manifest_path: Path,
    repo_root: Path,
    scenario: GridBacktestScenario,
    profile: GridThresholdProfile,
) -> GridScenarioEvaluation:
    candles = _load_fixture(
        _resolve_dataset_path(
            manifest_path=manifest_path,
            repo_root=repo_root,
            dataset_path=scenario.dataset_path,
        ),
    )
    run_candles = _apply_max_candles(candles, scenario.max_candles)
    config = _grid_config_for_candles(scenario=scenario, candles=run_candles)
    state = run_grid_backtest(config, run_candles)
    metrics = evaluate_grid_backtest(state, config, backtest_days=_backtest_days(run_candles))
    profitable_window_rate = _grid_profitable_window_rate(scenario=scenario, candles=candles)
    failure_reasons = _evaluate_grid_profile(
        metrics=metrics,
        profile=profile,
        profitable_window_rate=profitable_window_rate,
    )
    return GridScenarioEvaluation(
        name=scenario.name,
        symbol=scenario.symbol,
        profile=profile.name,
        metrics=metrics,
        profitable_window_rate=profitable_window_rate,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
    )


def _apply_max_candles(candles: Sequence[RawCandle], max_candles: int | None) -> Sequence[RawCandle]:
    if max_candles is None:
        return candles
    if max_candles <= 0:
        raise ValueError("max_candles must be positive.")
    if len(candles) < max_candles:
        raise ValueError(f"Fixture has {len(candles)} candles, expected at least {max_candles}.")
    return candles[:max_candles]


def _grid_config_for_candles(
    *,
    scenario: GridBacktestScenario,
    candles: Sequence[RawCandle],
) -> GridConfig:
    if not candles:
        raise ValueError(f"{scenario.name} has no candles.")
    start_price = _candle_close(candles[0])
    return GridConfig(
        symbol=scenario.symbol,
        p_min=start_price * (1 + scenario.p_min_pct_from_start),
        p_max=start_price * (1 + scenario.p_max_pct_from_start),
        n_levels=scenario.n_levels,
        capital_usdt=scenario.capital_usdt,
    )


def _grid_profitable_window_rate(
    *,
    scenario: GridBacktestScenario,
    candles: Sequence[RawCandle],
) -> float | None:
    if scenario.walk_forward_days is None and scenario.walk_forward_windows is None:
        return None
    if scenario.walk_forward_days is None or scenario.walk_forward_windows is None:
        raise ValueError("walk_forward_days and walk_forward_windows must be provided together.")
    candles_per_window = scenario.walk_forward_days * 24 * 4
    if candles_per_window <= 0 or scenario.walk_forward_windows <= 0:
        raise ValueError("walk-forward settings must be positive.")

    profitable_windows = 0
    for window_index in range(scenario.walk_forward_windows):
        start = window_index * candles_per_window
        window_candles = candles[start : start + candles_per_window]
        if len(window_candles) != candles_per_window:
            raise ValueError(
                f"{scenario.name} walk-forward window {window_index + 1} has "
                f"{len(window_candles)} candles, expected {candles_per_window}.",
            )
        config = _grid_config_for_candles(scenario=scenario, candles=window_candles)
        state = run_grid_backtest(config, window_candles)
        metrics = evaluate_grid_backtest(
            state,
            config,
            backtest_days=_backtest_days(window_candles),
        )
        if metrics.net_pnl_usdt > 0:
            profitable_windows += 1
    return profitable_windows / scenario.walk_forward_windows


def _evaluate_grid_profile(
    *,
    metrics: GridBacktestResult,
    profile: GridThresholdProfile,
    profitable_window_rate: float | None,
) -> tuple[str, ...]:
    failure_reasons: list[str] = []
    if metrics.completed_cycles < profile.min_completed_cycles:
        failure_reasons.append(
            f"completed_cycles {metrics.completed_cycles} < {profile.min_completed_cycles}",
        )
    if metrics.net_pnl_usdt < profile.min_net_pnl_usdt:
        failure_reasons.append(
            f"net_pnl_usdt {metrics.net_pnl_usdt} < {profile.min_net_pnl_usdt}",
        )
    if metrics.fee_coverage_ratio < profile.min_fee_coverage_ratio:
        failure_reasons.append(
            f"fee_coverage_ratio {metrics.fee_coverage_ratio} < {profile.min_fee_coverage_ratio}",
        )
    if metrics.annualized_yield_pct < profile.min_annualized_yield_pct:
        failure_reasons.append(
            f"annualized_yield_pct {metrics.annualized_yield_pct} < {profile.min_annualized_yield_pct}",
        )
    if metrics.max_unrealized_drawdown_pct > profile.max_unrealized_drawdown_pct:
        failure_reasons.append(
            "max_unrealized_drawdown_pct "
            f"{metrics.max_unrealized_drawdown_pct} > {profile.max_unrealized_drawdown_pct}",
        )
    if profile.min_profitable_window_rate is not None:
        if profitable_window_rate is None:
            failure_reasons.append("profitable_window_rate missing")
        elif profitable_window_rate < profile.min_profitable_window_rate:
            failure_reasons.append(
                f"profitable_window_rate {profitable_window_rate} < "
                f"{profile.min_profitable_window_rate}",
            )
    return tuple(failure_reasons)


def _serialize_grid_evaluation(evaluation: GridScenarioEvaluation) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": evaluation.name,
        "engine": "grid",
        "symbol": evaluation.symbol,
        "profile": evaluation.profile,
        "passed": evaluation.passed,
        "failure_reasons": list(evaluation.failure_reasons),
        "profitable_window_rate": evaluation.profitable_window_rate,
    }
    payload |= asdict(evaluation.metrics)
    return payload


def _validate_selected_names(
    *,
    selected_names: set[str] | None,
    results: Sequence[Mapping[str, object]],
) -> None:
    if selected_names is None:
        return
    observed = {str(result["name"]) for result in results if "name" in result}
    missing = selected_names - observed
    if missing:
        raise ValueError(f"Manifest suite references unknown scenarios: {sorted(missing)}")


def _mapping_payload(raw_value: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw_value, Mapping):
        raise TypeError(f"{label} must be an object.")
    return raw_value


def _str_value(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string.")
    return value


def _int_value(payload: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer.")
    return value


def _optional_int_value(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer.")
    return value


def _float_value(
    payload: Mapping[str, object],
    key: str,
    default: float | None = None,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be numeric.")
    return float(value)


def _optional_float_value(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be numeric.")
    return float(value)


async def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    await run_manifest_gate(
        manifest_path=args.manifest,
        mode=args.mode,
        output_path=_resolve_output_path(output=args.output, mode=args.mode),
        fee_rate_per_side=args.fee_rate_per_side,
        suite=args.suite,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BacktestGateError as exc:
        print(str(exc))
        raise SystemExit(1) from exc
