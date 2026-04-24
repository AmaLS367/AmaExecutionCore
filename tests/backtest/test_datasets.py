from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.backtest.datasets import (
    candles_for_lookback,
    fetch_candles_with_retry,
    load_dataset,
    save_dataset,
)
from backend.bybit_client.rest import BybitKline
from backend.market_data.contracts import MarketCandle


def _build_candles(count: int) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index * 5),
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=1000.0 + index,
        )
        for index in range(count)
    )


def test_candles_for_lookback_uses_interval_minutes() -> None:
    assert candles_for_lookback(interval="5", lookback_days=1) == 288


def test_save_and_load_dataset_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "btc_5m.json.gz"
    candles = _build_candles(3)

    save_dataset(
        path,
        symbol="BTCUSDT",
        interval="5",
        lookback_days=30,
        candles=candles,
        generated_at=datetime(2024, 1, 10, tzinfo=UTC),
    )

    dataset = load_dataset(path)

    assert dataset.symbol == "BTCUSDT"
    assert dataset.interval == "5"
    assert dataset.lookback_days == 30
    assert len(dataset.candles) == 3
    assert dataset.generated_at == "2024-01-10T00:00:00+00:00"


@pytest.mark.asyncio
async def test_fetch_candles_with_retry_retries_then_succeeds() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        def get_klines(
            self,
            *,
            symbol: str,
            interval: str,
            limit: int,
            category: str,
            end: int | None = None,
        ) -> list[BybitKline]:
            del symbol, interval, category, end
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary failure")
            opened_at = datetime(2024, 1, 1, tzinfo=UTC)
            return [
                BybitKline(
                    start_time=opened_at + timedelta(minutes=index * 5),
                    open_price=100.0 + index,
                    high_price=101.0 + index,
                    low_price=99.0 + index,
                    close_price=100.5 + index,
                    volume=1000.0 + index,
                    turnover=10000.0 + index,
                )
                for index in range(limit)
            ]

    client = _Client()
    candles = await fetch_candles_with_retry(
        client,
        symbol="BTCUSDT",
        interval="5",
        lookback_days=1,
        retries=2,
        base_delay_seconds=0,
    )

    assert client.calls == 2
    assert len(candles) == 288
