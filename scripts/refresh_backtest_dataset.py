from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from backend.backtest import load_manifest
from backend.backtest.datasets import SupportsKlineFetch, fetch_candles_with_retry, save_dataset
from backend.bybit_client.rest import BybitRESTClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh checked-in backtest datasets from Bybit spot candles.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("scripts/fixtures/backtest_manifest.json"),
    )
    parser.add_argument(
        "--output-summary",
        type=Path,
        default=Path("scripts/fixtures/regression/regression_dataset_refresh.json"),
    )
    return parser


def _resolve_repo_root(manifest_path: Path) -> Path:
    return manifest_path.resolve().parents[2]


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper())


def _resolve_dataset_path(
    *,
    manifest_path: Path,
    repo_root: Path,
    dataset_path: str,
) -> Path:
    raw_path = Path(dataset_path)
    if raw_path.is_absolute():
        return raw_path
    if raw_path.parts and raw_path.parts[0] == "scripts":
        return (repo_root / raw_path).resolve()
    return (manifest_path.parent / raw_path).resolve()


async def refresh_manifest_datasets(
    *,
    manifest_path: Path,
    output_summary_path: Path,
    client: SupportsKlineFetch | None = None,
) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    repo_root = _resolve_repo_root(manifest_path)
    rest_client = client or BybitRESTClient()
    refreshed: list[dict[str, object]] = []
    generated_at = datetime.now(UTC)

    for scenario in manifest.scenarios:
        dataset_path = _resolve_dataset_path(
            manifest_path=manifest_path,
            repo_root=repo_root,
            dataset_path=scenario.dataset_path,
        )
        candles = await fetch_candles_with_retry(
            rest_client,
            symbol=scenario.symbol,
            interval=scenario.interval,
            lookback_days=scenario.lookback_days,
            retries=3,
            base_delay_seconds=2.0,
        )
        save_dataset(
            dataset_path,
            symbol=scenario.symbol,
            interval=scenario.interval,
            lookback_days=scenario.lookback_days,
            candles=candles,
            generated_at=generated_at,
        )
        refreshed.append(
            {
                "name": scenario.name,
                "symbol": scenario.symbol,
                "interval": scenario.interval,
                "lookback_days": scenario.lookback_days,
                "dataset_path": scenario.dataset_path,
                "candles": len(candles),
                "generated_at": generated_at.isoformat(),
            },
        )
        print(
            f"[REFRESHED] {scenario.name} symbol={scenario.symbol} interval={scenario.interval} candles={len(candles)}",
        )

    report: dict[str, object] = {
        "generated_at": generated_at.isoformat(),
        "manifest": str(manifest_path.as_posix()),
        "datasets": refreshed,
    }
    output_summary_path.parent.mkdir(parents=True, exist_ok=True)
    output_summary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


async def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    await refresh_manifest_datasets(
        manifest_path=args.manifest,
        output_summary_path=args.output_summary,
    )


if __name__ == "__main__":
    asyncio.run(main())
