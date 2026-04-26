from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.backtest.datasets import save_dataset
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal
from scripts.backtest_gate import run_manifest_gate
from scripts.refresh_backtest_dataset import refresh_manifest_datasets


@pytest.mark.asyncio
async def test_run_manifest_gate_regression_uses_fixture_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "btc.json.gz"
    save_dataset(
        fixture_path,
        symbol="BTCUSDT",
        interval="5",
        lookback_days=365,
        candles=(
            MarketCandle(
                opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            ),
            MarketCandle(
                opened_at=datetime(2024, 1, 1, 0, 5, tzinfo=UTC),
                high=102.0,
                low=100.0,
                close=101.0,
                volume=1005.0,
            ),
        ),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "regression_v1": {
                        "min_closed_trades": 0,
                        "min_win_rate": 0,
                        "min_profit_factor": 0,
                        "require_positive_expectancy": False,
                        "max_drawdown_pct": 1,
                    },
                },
                "scenarios": [
                    {
                        "name": "btc",
                        "family": "scalping",
                        "strategy": "vwap_reversion",
                        "symbol": "BTCUSDT",
                        "interval": "5",
                        "lookback_days": 365,
                        "live_lookback_days": 180,
                        "dataset_path": fixture_path.relative_to(tmp_path).as_posix(),
                        "risk_amount_usd": 100.0,
                        "starting_equity_usd": 10000.0,
                        "max_hold_candles": 20,
                        "min_rrr": 1.5,
                        "regression_profile": "regression_v1",
                        "live_profile": "regression_v1",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "regression.json"

    class _Strategy:
        required_candle_count = 1

        async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
            del snapshot
            return StrategySignal(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=99.0,
                target=101.0,
            )

    monkeypatch.setattr(
        "backend.backtest.gate.build_scalping_strategy",
        lambda **_: _Strategy(),
    )

    report = await run_manifest_gate(
        manifest_path=manifest_path,
        mode="regression",
        output_path=output_path,
        fee_rate_per_side=0.001,
    )

    assert report["all_passed"] is True
    assert output_path.exists()


@pytest.mark.asyncio
async def test_run_manifest_gate_live_fails_closed_after_retry_exhaustion(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "live_v1": {
                        "min_closed_trades": 0,
                        "min_win_rate": 0,
                        "min_profit_factor": 0,
                        "require_positive_expectancy": False,
                        "max_drawdown_pct": 1,
                    },
                },
                "scenarios": [
                    {
                        "name": "btc",
                        "family": "scalping",
                        "strategy": "vwap_reversion",
                        "symbol": "BTCUSDT",
                        "interval": "5",
                        "lookback_days": 365,
                        "live_lookback_days": 1,
                        "dataset_path": "ignored.json.gz",
                        "risk_amount_usd": 100.0,
                        "starting_equity_usd": 10000.0,
                        "max_hold_candles": 20,
                        "min_rrr": 1.5,
                        "regression_profile": "live_v1",
                        "live_profile": "live_v1",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    class _FailingClient:
        def get_klines(self, **_: object) -> object:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run_manifest_gate(
            manifest_path=manifest_path,
            mode="live",
            output_path=tmp_path / "live.json",
            fee_rate_per_side=0.001,
            client=_FailingClient(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_refresh_manifest_datasets_writes_summary(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    dataset_path = tmp_path / "btc.json.gz"
    manifest_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "regression_v1": {
                        "min_closed_trades": 0,
                        "min_win_rate": 0,
                        "min_profit_factor": 0,
                        "require_positive_expectancy": False,
                        "max_drawdown_pct": 1,
                    },
                },
                "scenarios": [
                    {
                        "name": "btc",
                        "family": "scalping",
                        "strategy": "vwap_reversion",
                        "symbol": "BTCUSDT",
                        "interval": "5",
                        "lookback_days": 1,
                        "live_lookback_days": 1,
                        "dataset_path": dataset_path.relative_to(tmp_path).as_posix(),
                        "risk_amount_usd": 100.0,
                        "starting_equity_usd": 10000.0,
                        "max_hold_candles": 20,
                        "min_rrr": 1.5,
                        "regression_profile": "regression_v1",
                        "live_profile": "regression_v1",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    class _Client:
        def get_klines(
            self,
            *,
            symbol: str,
            interval: str,
            limit: int,
            category: str,
            end: int | None = None,
        ) -> object:
            del symbol, interval, category, end
            from datetime import UTC, datetime, timedelta

            from backend.bybit_client.rest import BybitKline

            opened_at = datetime(2024, 1, 1, tzinfo=UTC)
            return [
                BybitKline(
                    start_time=opened_at + timedelta(minutes=index * 5),
                    open_price=100.0,
                    high_price=101.0,
                    low_price=99.0,
                    close_price=100.0,
                    volume=1000.0,
                    turnover=10000.0,
                )
                for index in range(limit)
            ]

    summary_path = tmp_path / "refresh.json"
    report = await refresh_manifest_datasets(
        manifest_path=manifest_path,
        output_summary_path=summary_path,
        client=_Client(),  # type: ignore[arg-type]
    )

    assert summary_path.exists()
    assert dataset_path.exists()
    assert len(report["datasets"]) == 1


@pytest.mark.asyncio
async def test_refresh_manifest_datasets_rejects_partial_fetch_before_writing(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    dataset_path = tmp_path / "btc.json.gz"
    manifest_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "regression_v1": {
                        "min_closed_trades": 0,
                        "min_win_rate": 0,
                        "min_profit_factor": 0,
                        "require_positive_expectancy": False,
                        "max_drawdown_pct": 1,
                    },
                },
                "scenarios": [
                    {
                        "name": "btc",
                        "family": "scalping",
                        "strategy": "vwap_reversion",
                        "symbol": "BTCUSDT",
                        "interval": "5",
                        "lookback_days": 1,
                        "live_lookback_days": 1,
                        "dataset_path": dataset_path.relative_to(tmp_path).as_posix(),
                        "risk_amount_usd": 100.0,
                        "starting_equity_usd": 10000.0,
                        "max_hold_candles": 20,
                        "min_rrr": 1.5,
                        "regression_profile": "regression_v1",
                        "live_profile": "regression_v1",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    class _ShortClient:
        def get_klines(
            self,
            *,
            symbol: str,
            interval: str,
            limit: int,
            category: str,
            end: int | None = None,
        ) -> object:
            del symbol, interval, limit, category, end
            from backend.bybit_client.rest import BybitKline

            return [
                BybitKline(
                    start_time=datetime(2024, 1, 1, tzinfo=UTC),
                    open_price=100.0,
                    high_price=101.0,
                    low_price=99.0,
                    close_price=100.0,
                    volume=1000.0,
                    turnover=10000.0,
                ),
            ]

    summary_path = tmp_path / "refresh.json"
    with pytest.raises(ValueError, match="Partial candle fetch"):
        await refresh_manifest_datasets(
            manifest_path=manifest_path,
            output_summary_path=summary_path,
            client=_ShortClient(),  # type: ignore[arg-type]
        )

    assert not summary_path.exists()
    assert not dataset_path.exists()
