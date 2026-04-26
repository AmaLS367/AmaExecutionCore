from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.run_backtest import (
    BacktestExpectationError,
    BacktestThresholds,
    _evaluate_thresholds,
    _load_candles,
    _load_fixture_payload,
)


def test_load_fixture_payload_parses_symbol_interval_and_candles(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "symbol": "BTCUSDT",
                "interval": "15",
                "candles": [
                    {
                        "opened_at": "2024-01-01T00:00:00+00:00",
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 12.5,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    payload = _load_fixture_payload(fixture_path)

    assert payload.symbol == "BTCUSDT"
    assert payload.interval == "15"
    assert len(payload.candles) == 1
    assert payload.candles[0].close == 100.0


def test_load_candles_accepts_smoke_fixture_without_dataset_metadata(tmp_path: Path) -> None:
    fixture_path = tmp_path / "smoke.json"
    fixture_path.write_text(
        json.dumps(
            {
                "symbol": "ethusdt",
                "interval": "5",
                "candles": [
                    {
                        "opened_at": "2024-01-01T00:00:00+00:00",
                        "open": 99.5,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 12.5,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    symbol, interval, lookback_days, candles = _load_candles(
        args=Namespace(
            fixture=fixture_path,
            symbol=None,
            interval=None,
            candles=None,
            lookback_days=None,
        ),
    )

    assert symbol == "ETHUSDT"
    assert interval == "5"
    assert lookback_days is None
    assert len(candles) == 1
    assert candles[0].open == 99.5


def test_evaluate_thresholds_raises_when_closed_trade_count_is_too_low() -> None:
    thresholds = BacktestThresholds(min_closed_trades=2)

    with pytest.raises(BacktestExpectationError, match="closed trades"):
        _evaluate_thresholds(
            closed_trades=1,
            win_rate=None,
            profit_factor=None,
            max_drawdown=None,
            thresholds=thresholds,
        )


def test_evaluate_thresholds_accepts_matching_metrics() -> None:
    thresholds = BacktestThresholds(
        min_closed_trades=2,
        min_win_rate=0.5,
        max_drawdown=50.0,
    )

    _evaluate_thresholds(
        closed_trades=2,
        win_rate=0.75,
        profit_factor=None,
        max_drawdown=20.0,
        thresholds=thresholds,
    )
