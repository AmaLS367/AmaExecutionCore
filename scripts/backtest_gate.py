from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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

GateMode = Literal["regression", "live"]


class BacktestGateError(RuntimeError):
    """Raised when one or more manifest scenarios fail their profile thresholds."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run manifest-driven backtest gates.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("scripts/fixtures/backtest_manifest.json"),
    )
    parser.add_argument("--mode", choices=("regression", "live"), required=True)
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
    client: SupportsKlineFetch | None = None,
) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    repo_root = _resolve_repo_root(manifest_path)
    results: list[dict[str, object]] = []
    live_client = client
    if mode == "live" and live_client is None:
        live_client = BybitRESTClient()

    for scenario in manifest.scenarios:
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

    report = {
        "mode": mode,
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


async def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    await run_manifest_gate(
        manifest_path=args.manifest,
        mode=args.mode,
        output_path=_resolve_output_path(output=args.output, mode=args.mode),
        fee_rate_per_side=args.fee_rate_per_side,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BacktestGateError as exc:
        print(str(exc))
        raise SystemExit(1) from exc
