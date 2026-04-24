from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.vwap_reversion_v2 import VWAPReversionStrategyV2


def _build_snapshot(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    current_day_count: int = 12,
) -> MarketSnapshot:
    candle_volumes = volumes or [100.0] * len(closes)
    previous_day_count = len(closes) - current_day_count
    previous_day_start = datetime(2024, 1, 1, 20, 0, tzinfo=UTC)
    current_day_start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    candles: list[MarketCandle] = []

    for index in range(previous_day_count):
        close = closes[index]
        candles.append(
            MarketCandle(
                opened_at=previous_day_start + timedelta(minutes=5 * index),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=candle_volumes[index],
            ),
        )

    for index in range(current_day_count):
        close_index = previous_day_count + index
        close = closes[close_index]
        candles.append(
            MarketCandle(
                opened_at=current_day_start + timedelta(minutes=5 * index),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=candle_volumes[close_index],
            ),
        )

    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=tuple(candles))


def _patch_common_indicators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    vwap: float,
    rsi_value: float,
    atr_value: float,
    adx_value: float = 20.0,
    volatility: float = 0.01,
) -> None:
    def _vwap(_snapshot: MarketSnapshot) -> float:
        return vwap

    def _rsi(_closes: list[float], _period: int) -> list[float]:
        return [rsi_value]

    def _atr(_highs: list[float], _lows: list[float], _closes: list[float], _period: int) -> list[float]:
        return [atr_value]

    def _regime(_candles: object, *, period: int = 14) -> dict[str, object]:
        del period
        return {
            "adx_value": adx_value,
            "regime": "ranging",
            "recommended_strategy": "vwap_reversion",
        }

    monkeypatch.setattr(
        "backend.strategy_engine.vwap_reversion_v2._calculate_intraday_vwap",
        _vwap,
    )
    monkeypatch.setattr(
        "backend.strategy_engine.vwap_reversion_v2._calculate_rsi",
        _rsi,
    )
    monkeypatch.setattr(
        "backend.strategy_engine.vwap_reversion_v2._calculate_atr",
        _atr,
    )
    monkeypatch.setattr(
        "backend.strategy_engine.vwap_reversion_v2.detect_regime",
        _regime,
    )
    monkeypatch.setattr(
        VWAPReversionStrategyV2,
        "_realized_volatility",
        staticmethod(lambda _snapshot: volatility),
    )


@pytest.mark.asyncio
async def test_v2_signal_when_all_conditions_met(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    volumes = [100.0] * 49 + [180.0]
    _patch_common_indicators(monkeypatch, vwap=100.5, rsi_value=25.0, atr_value=0.2)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.strategy_version == "vwap-reversion-v2"


@pytest.mark.asyncio
async def test_v2_returns_none_when_adx_high(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    _patch_common_indicators(monkeypatch, vwap=100.0, rsi_value=25.0, atr_value=1.0, adx_value=30.0)

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is None


@pytest.mark.asyncio
async def test_v2_returns_none_when_volatility_high(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    _patch_common_indicators(monkeypatch, vwap=100.0, rsi_value=25.0, atr_value=1.0, volatility=0.03)

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is None


@pytest.mark.asyncio
async def test_v2_returns_none_when_volume_low(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    volumes = [100.0] * 49 + [90.0]
    _patch_common_indicators(monkeypatch, vwap=100.0, rsi_value=25.0, atr_value=1.0)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_v2_returns_none_when_no_reversal_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [99.0, 98.5, 98.0]
    volumes = [100.0] * 49 + [180.0]
    _patch_common_indicators(monkeypatch, vwap=100.0, rsi_value=25.0, atr_value=1.0)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is None


@pytest.mark.asyncio
async def test_v2_returns_none_when_insufficient_day_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    _patch_common_indicators(monkeypatch, vwap=100.0, rsi_value=25.0, atr_value=1.0)

    signal = await strategy.generate_signal(_build_snapshot(closes, current_day_count=3))

    assert signal is None


@pytest.mark.asyncio
async def test_v2_long_stop_below_entry_by_atr_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    volumes = [100.0] * 49 + [180.0]
    _patch_common_indicators(monkeypatch, vwap=104.0, rsi_value=25.0, atr_value=2.0)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.stop == pytest.approx(signal.entry - 3.0)


@pytest.mark.asyncio
async def test_v2_short_stop_above_entry_by_atr_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [103.0, 102.0, 101.0]
    volumes = [100.0] * 49 + [180.0]
    _patch_common_indicators(monkeypatch, vwap=96.8, rsi_value=75.0, atr_value=2.0)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.stop == pytest.approx(signal.entry + 3.0)


@pytest.mark.asyncio
async def test_v2_target_satisfies_min_rrr_after_costs(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = VWAPReversionStrategyV2()
    closes = [100.0] * 47 + [97.0, 98.0, 99.0]
    volumes = [100.0] * 49 + [180.0]
    _patch_common_indicators(monkeypatch, vwap=100.5, rsi_value=25.0, atr_value=0.2)

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    risk = signal.entry - signal.stop
    reward = signal.target - signal.entry
    assert (reward / risk) >= strategy.min_rrr
