from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.ts_momentum_strategy import TSMomentumStrategy


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


def _base_closes() -> list[float]:
    return [100.0 + (0.05 * index) for index in range(293)]


def _patch_indicators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ema_fast: list[float],
    ema_slow: list[float],
    atr_value: float,
    volatility: float,
) -> None:
    def _ema(values: list[float], period: int) -> list[float]:
        del values
        return ema_fast if period == 96 else ema_slow

    def _atr(_highs: list[float], _lows: list[float], _closes: list[float], _period: int) -> list[float]:
        return [atr_value]

    monkeypatch.setattr("backend.strategy_engine.ts_momentum_strategy._calculate_ema", _ema)
    monkeypatch.setattr("backend.strategy_engine.ts_momentum_strategy._calculate_atr", _atr)
    monkeypatch.setattr(
        TSMomentumStrategy,
        "_realized_volatility",
        staticmethod(lambda _snapshot: volatility),
    )


def test_momentum_score_positive_in_uptrend(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    _patch_indicators(
        monkeypatch,
        ema_fast=[110.0 + (0.01 * index) for index in range(293)],
        ema_slow=[105.0 + (0.01 * index) for index in range(293)],
        atr_value=2.0,
        volatility=0.01,
    )

    score = strategy.compute_momentum_score(_build_snapshot(closes))

    assert score is not None
    assert score > 0


def test_momentum_score_none_when_regime_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    _patch_indicators(
        monkeypatch,
        ema_fast=[100.0] * 293,
        ema_slow=[105.0] * 293,
        atr_value=2.0,
        volatility=0.01,
    )

    score = strategy.compute_momentum_score(_build_snapshot(closes))

    assert score is None


def test_momentum_score_none_when_volatility_high(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    _patch_indicators(
        monkeypatch,
        ema_fast=[110.0 + (0.01 * index) for index in range(293)],
        ema_slow=[105.0 + (0.01 * index) for index in range(293)],
        atr_value=2.0,
        volatility=0.03,
    )

    score = strategy.compute_momentum_score(_build_snapshot(closes))

    assert score is None


@pytest.mark.asyncio
async def test_signal_does_not_require_breakout_above_4bar_high(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    closes[-5:] = [112.0, 113.0, 114.0, 115.0, 114.5]
    volumes = [100.0] * 292 + [200.0]
    monkeypatch.setattr(strategy, "compute_momentum_score", lambda _snapshot: 1.0)
    monkeypatch.setattr("backend.strategy_engine.ts_momentum_strategy._calculate_atr", lambda *_args: [2.0])

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.entry == closes[-1]


@pytest.mark.asyncio
async def test_signal_requires_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    closes[-5:] = [112.0, 113.0, 114.0, 115.0, 116.0]
    volumes = [100.0] * 292 + [120.0]
    monkeypatch.setattr(strategy, "compute_momentum_score", lambda _snapshot: 1.0)
    monkeypatch.setattr("backend.strategy_engine.ts_momentum_strategy._calculate_atr", lambda *_args: [2.0])

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_long_only(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = TSMomentumStrategy()
    closes = _base_closes()
    closes[-5:] = [112.0, 113.0, 114.0, 115.0, 116.0]
    volumes = [100.0] * 292 + [200.0]
    monkeypatch.setattr(strategy, "compute_momentum_score", lambda _snapshot: 1.0)
    monkeypatch.setattr("backend.strategy_engine.ts_momentum_strategy._calculate_atr", lambda *_args: [2.0])

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.direction == "long"
