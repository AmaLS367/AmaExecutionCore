from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.breakout_strategy import BreakoutStrategy


def _build_snapshot(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candle_highs = highs or [close + 1.0 for close in closes]
    candle_lows = lows or [close - 1.0 for close in closes]
    candle_volumes = volumes or [100.0] * len(closes)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=15 * index),
            open=close,
            high=candle_highs[index],
            low=candle_lows[index],
            close=close,
            volume=candle_volumes[index],
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="15", candles=candles)


def _patch_indicators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ema_values: list[float],
    atr_value: float,
    volatility: float,
) -> None:
    def _ema(values: list[float], period: int) -> list[float]:
        del values, period
        return ema_values

    def _atr(_highs: list[float], _lows: list[float], _closes: list[float], _period: int) -> list[float]:
        return [atr_value]

    monkeypatch.setattr("backend.strategy_engine.breakout_strategy._calculate_ema", _ema)
    monkeypatch.setattr("backend.strategy_engine.breakout_strategy._calculate_atr", _atr)
    monkeypatch.setattr(
        BreakoutStrategy,
        "_realized_volatility",
        staticmethod(lambda _snapshot: volatility),
    )


@pytest.mark.asyncio
async def test_long_signal_when_close_breaks_above_range_with_high_volume_and_price_above_ema50(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = BreakoutStrategy()
    closes = [100.0] * 50 + [106.0]
    highs = [104.0] * 30 + [105.0] * 20 + [107.0]
    lows = [98.0] * 51
    volumes = [100.0] * 50 + [250.0]
    _patch_indicators(monkeypatch, ema_values=[100.0] * 51, atr_value=2.0, volatility=0.01)

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, lows=lows, volumes=volumes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.stop == pytest.approx(104.0)
    assert signal.target == pytest.approx(110.0)


@pytest.mark.asyncio
async def test_short_signal_when_close_breaks_below_range_with_high_volume_and_price_below_ema50(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = BreakoutStrategy()
    closes = [100.0] * 50 + [94.0]
    highs = [102.0] * 51
    lows = [96.0] * 30 + [95.0] * 20 + [93.0]
    volumes = [100.0] * 50 + [250.0]
    _patch_indicators(monkeypatch, ema_values=[100.0] * 51, atr_value=2.0, volatility=0.01)

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, lows=lows, volumes=volumes))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop == pytest.approx(96.0)
    assert signal.target == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_returns_none_when_volume_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = BreakoutStrategy()
    closes = [100.0] * 50 + [106.0]
    highs = [104.0] * 30 + [105.0] * 20 + [107.0]
    lows = [98.0] * 51
    volumes = [100.0] * 50 + [150.0]
    _patch_indicators(monkeypatch, ema_values=[100.0] * 51, atr_value=2.0, volatility=0.01)

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, lows=lows, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_price_breaks_high_but_below_ema50(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = BreakoutStrategy()
    closes = [100.0] * 50 + [106.0]
    highs = [104.0] * 30 + [105.0] * 20 + [107.0]
    lows = [98.0] * 51
    volumes = [100.0] * 50 + [250.0]
    _patch_indicators(monkeypatch, ema_values=[108.0] * 51, atr_value=2.0, volatility=0.01)

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, lows=lows, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_volatility_too_high(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = BreakoutStrategy()
    closes = [100.0] * 50 + [106.0]
    highs = [104.0] * 30 + [105.0] * 20 + [107.0]
    lows = [98.0] * 51
    volumes = [100.0] * 50 + [250.0]
    _patch_indicators(monkeypatch, ema_values=[100.0] * 51, atr_value=2.0, volatility=0.05)

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, lows=lows, volumes=volumes))

    assert signal is None
