from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.ema_pullback_strategy import EMAPullbackStrategy


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
            opened_at=opened_at + timedelta(minutes=5 * index),
            open=close,
            high=candle_highs[index],
            low=candle_lows[index],
            close=close,
            volume=candle_volumes[index],
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


def _patch_indicators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fast_ema: list[float],
    slow_ema: list[float],
    rsi_value: float,
    atr_value: float,
) -> None:
    def _ema(values: list[float], period: int) -> list[float]:
        del values
        return fast_ema if period == 20 else slow_ema

    def _rsi(_closes: list[float], _period: int) -> list[float]:
        return [rsi_value]

    def _atr(_highs: list[float], _lows: list[float], _closes: list[float], _period: int) -> list[float]:
        return [atr_value]

    monkeypatch.setattr("backend.strategy_engine.ema_pullback_strategy._calculate_ema", _ema)
    monkeypatch.setattr("backend.strategy_engine.ema_pullback_strategy._calculate_rsi", _rsi)
    monkeypatch.setattr("backend.strategy_engine.ema_pullback_strategy._calculate_atr", _atr)


@pytest.mark.asyncio
async def test_long_signal_when_uptrend_pullback_and_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy()
    closes = [100.0] * 68 + [101.0]
    lows = [98.0] * 64 + [99.5, 99.8, 100.2, 99.9, 100.0]
    volumes = [100.0] * 68 + [140.0]
    _patch_indicators(
        monkeypatch,
        fast_ema=[99.0] * 64 + [99.2, 99.4, 99.6, 99.8, 100.0],
        slow_ema=[95.0] * 69,
        rsi_value=55.0,
        atr_value=2.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes, lows=lows, volumes=volumes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.strategy_version == "ema-pullback-v1"


@pytest.mark.asyncio
async def test_short_signal_when_downtrend_pullback(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy()
    closes = [100.0] * 68 + [99.0]
    highs = [102.0] * 64 + [100.5, 100.2, 99.8, 100.1, 100.0]
    volumes = [100.0] * 68 + [140.0]
    _patch_indicators(
        monkeypatch,
        fast_ema=[101.0] * 64 + [100.8, 100.6, 100.4, 100.2, 100.0],
        slow_ema=[105.0] * 69,
        rsi_value=45.0,
        atr_value=2.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes, highs=highs, volumes=volumes))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.strategy_version == "ema-pullback-v1"


@pytest.mark.asyncio
async def test_returns_none_when_trend_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy()
    closes = [100.0] * 69
    _patch_indicators(
        monkeypatch,
        fast_ema=[100.0] * 69,
        slow_ema=[100.0] * 69,
        rsi_value=55.0,
        atr_value=2.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_no_pullback(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy()
    closes = [100.0] * 68 + [101.0]
    lows = [97.0] * 69
    volumes = [100.0] * 68 + [140.0]
    _patch_indicators(
        monkeypatch,
        fast_ema=[99.0] * 64 + [99.2, 99.4, 99.6, 99.8, 100.0],
        slow_ema=[95.0] * 69,
        rsi_value=55.0,
        atr_value=1.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes, lows=lows, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_rsi_not_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy()
    closes = [100.0] * 68 + [101.0]
    lows = [98.0] * 64 + [99.5, 99.8, 100.2, 99.9, 100.0]
    volumes = [100.0] * 68 + [140.0]
    _patch_indicators(
        monkeypatch,
        fast_ema=[99.0] * 64 + [99.2, 99.4, 99.6, 99.8, 100.0],
        slow_ema=[95.0] * 69,
        rsi_value=35.0,
        atr_value=2.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes, lows=lows, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_rrr_below_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = EMAPullbackStrategy(target_r_multiple=1.2, min_rrr=1.5)
    closes = [100.0] * 68 + [101.0]
    lows = [98.0] * 64 + [99.5, 99.8, 100.2, 99.9, 100.0]
    volumes = [100.0] * 68 + [140.0]
    _patch_indicators(
        monkeypatch,
        fast_ema=[99.0] * 64 + [99.2, 99.4, 99.6, 99.8, 100.0],
        slow_ema=[95.0] * 69,
        rsi_value=55.0,
        atr_value=2.0,
    )

    signal = await strategy.generate_signal(_build_snapshot(closes, lows=lows, volumes=volumes))

    assert signal is None
