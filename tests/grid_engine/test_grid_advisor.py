import gzip
import json
from collections.abc import Mapping
from pathlib import Path

from backend.grid_engine.grid_advisor import suggest_grid
from backend.grid_engine.grid_backtester import RawCandle


def test_suggest_grid_b1_gate_with_xrp_fixture_candles() -> None:
    candles = _load_fixture(Path("scripts/fixtures/regression/xrpusdt_15m_365d.json.gz"))[:50]

    config = suggest_grid(candles, capital_usdt=20.0)

    assert config.step_pct >= 0.005
    assert config.p_min > 0
    assert config.p_max > config.p_min
    assert config.n_levels >= 4


def _load_fixture(path: Path) -> list[RawCandle]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        loaded: object = json.load(file)
    if not isinstance(loaded, Mapping):
        raise TypeError("Regression fixture must be a JSON object.")
    candles = loaded.get("candles")
    if not isinstance(candles, list):
        raise TypeError("Regression fixture must contain candles list.")
    return [_normalize_candle(candle) for candle in candles]


def _normalize_candle(candle: object) -> RawCandle:
    if not isinstance(candle, Mapping):
        raise TypeError(f"Unsupported candle format: {candle!r}")
    normalized: dict[str, object] = {}
    for key, value in candle.items():
        if not isinstance(key, str):
            raise TypeError(f"Candle key must be a string, got {key!r}.")
        normalized[key] = value
    return normalized
