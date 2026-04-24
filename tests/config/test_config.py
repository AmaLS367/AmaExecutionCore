from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.config import Settings, _split_symbols


def _base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "trading_mode": "shadow",
        "bybit_testnet_api_key": "",
        "bybit_testnet_api_secret": "",
        "bybit_api_key": "",
        "bybit_api_secret": "",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_active_api_credentials_follow_selected_environment() -> None:
    testnet_settings = _base_settings(
        bybit_testnet=True,
        bybit_testnet_api_key="test-key",
        bybit_testnet_api_secret="test-secret",
    )
    mainnet_settings = _base_settings(
        bybit_testnet=False,
        bybit_api_key="main-key",
        bybit_api_secret="main-secret",
    )

    assert testnet_settings.active_api_key == "test-key"
    assert testnet_settings.active_api_secret == "test-secret"
    assert mainnet_settings.active_api_key == "main-key"
    assert mainnet_settings.active_api_secret == "main-secret"


def test_database_url_must_be_set() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must be set"):
        Settings(database_url="")


def test_parse_symbol_lists_normalizes_strings_and_iterables() -> None:
    settings_from_string = _base_settings(signal_loop_symbols=" btcusdt , ethusdt ")
    settings_from_iterable = _base_settings(scalping_symbols=[" btcusdt ", "ETHUSDT", ""])

    assert settings_from_string.signal_loop_symbols == ["BTCUSDT", "ETHUSDT"]
    assert settings_from_iterable.scalping_symbols == ["BTCUSDT", "ETHUSDT"]


def test_parse_symbol_lists_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="Unsupported symbol list value"):
        _base_settings(signal_loop_symbols=123)


def test_signal_loop_strategy_is_normalized_and_must_not_be_empty() -> None:
    settings = _base_settings(signal_loop_strategy=" RSI_EMA ")

    assert settings.signal_loop_strategy == "rsi_ema"

    with pytest.raises(ValidationError, match="SIGNAL_LOOP_STRATEGY must not be empty"):
        _base_settings(signal_loop_strategy="   ")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"trading_mode": "demo", "bybit_testnet": True, "bybit_testnet_api_secret": "secret"},
            "BYBIT_TESTNET_API_KEY must be set",
        ),
        (
            {"trading_mode": "demo", "bybit_testnet": True, "bybit_testnet_api_key": "key"},
            "BYBIT_TESTNET_API_SECRET must be set",
        ),
        (
            {"trading_mode": "real", "bybit_testnet": False, "bybit_api_secret": "secret"},
            "BYBIT_API_KEY must be set",
        ),
        (
            {"trading_mode": "real", "bybit_testnet": False, "bybit_api_key": "key"},
            "BYBIT_API_SECRET must be set",
        ),
    ],
)
def test_api_keys_are_required_outside_shadow(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _base_settings(**kwargs)


def test_split_symbols_handles_blank_values() -> None:
    assert _split_symbols("  ") == []
    assert _split_symbols("btc, eth ,, sol") == ["BTC", "ETH", "SOL"]
