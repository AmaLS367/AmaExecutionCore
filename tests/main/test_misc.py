from __future__ import annotations

import asyncio

import pytest

from backend.bybit_client.exceptions import BybitConnectionError
from backend.config import settings
from backend.main import NullRestClient, _validate_runner_configuration, create_app, health_check


class _PassiveRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


def test_health_check_returns_obfuscated_api_key() -> None:
    settings.bybit_testnet = False
    settings.bybit_api_key = "abcd1234"

    payload = asyncio.run(health_check())

    assert payload["status"] == "ok"
    assert payload["api_key_configured"] is True
    assert payload["bybit_testnet"] is False


def test_validate_runner_configuration_raises_on_symbol_overlap() -> None:
    settings.signal_loop_enabled = True
    settings.signal_loop_symbols = ["BTCUSDT", "ETHUSDT"]
    settings.scalping_enabled = True
    settings.scalping_symbols = ["BTCUSDT"]

    with pytest.raises(RuntimeError, match="overlap"):
        _validate_runner_configuration()


def test_create_app_uses_null_rest_client_when_bybit_is_unavailable(
    sqlite_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise BybitConnectionError("offline")

    monkeypatch.setattr("backend.main.BybitRESTClient", _raise)

    app = create_app(session_factory=sqlite_session_factory)

    assert isinstance(app.state.rest_client, NullRestClient)
