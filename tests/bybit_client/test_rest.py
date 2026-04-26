from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.bybit_client.exceptions import (
    BybitAPIError,
    BybitConnectionError,
    InvalidOrderParamsError,
)
from backend.bybit_client.rest import BybitKline, BybitRESTClient
from backend.config import settings


class FakeHTTPSession:
    def __init__(self) -> None:
        self.init_kwargs: dict[str, object] | None = None
        self.wallet_balance_response: dict[str, Any] = {"retCode": 0, "result": {"list": []}}
        self.instruments_info_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"list": [{"symbol": "BTCUSDT"}]},
        }
        self.kline_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"list": [["1710000000000", "1", "2", "0.5", "1.5", "10", "20"]]},
        }
        self.tickers_response: dict[str, Any] = {
            "retCode": 0,
            "result": {"list": [{"lastPrice": "123.45"}]},
        }
        self.place_order_response: dict[str, Any] = {"retCode": 0, "result": {"orderId": "abc"}}
        self.cancel_order_response: dict[str, Any] = {"retCode": 0, "result": {"orderId": "cancelled"}}
        self.open_orders_response: dict[str, Any] = {"retCode": 0, "result": {"list": []}}
        self.order_history_response: dict[str, Any] = {"retCode": 0, "result": {"list": []}}
        self.last_place_order_params: dict[str, Any] | None = None
        self.last_cancel_order_params: dict[str, Any] | None = None
        self.last_status_params: dict[str, Any] | None = None
        self.raise_on_wallet_balance: Exception | None = None
        self.raise_on_instruments_info: Exception | None = None
        self.raise_on_kline: Exception | None = None
        self.raise_on_tickers: Exception | None = None
        self.raise_on_place_order: Exception | None = None
        self.raise_on_cancel_order: Exception | None = None
        self.raise_on_open_orders: Exception | None = None

    def get_wallet_balance(self, **_: object) -> dict[str, Any]:
        if self.raise_on_wallet_balance is not None:
            raise self.raise_on_wallet_balance
        return self.wallet_balance_response

    def get_instruments_info(self, **_: object) -> dict[str, Any]:
        if self.raise_on_instruments_info is not None:
            raise self.raise_on_instruments_info
        return self.instruments_info_response

    def get_kline(self, **_: object) -> dict[str, Any]:
        if self.raise_on_kline is not None:
            raise self.raise_on_kline
        return self.kline_response

    def get_tickers(self, **_: object) -> dict[str, Any]:
        if self.raise_on_tickers is not None:
            raise self.raise_on_tickers
        return self.tickers_response

    def place_order(self, **params: object) -> dict[str, Any]:
        if self.raise_on_place_order is not None:
            raise self.raise_on_place_order
        self.last_place_order_params = params
        return self.place_order_response

    def cancel_order(self, **params: object) -> dict[str, Any]:
        if self.raise_on_cancel_order is not None:
            raise self.raise_on_cancel_order
        self.last_cancel_order_params = params
        return self.cancel_order_response

    def get_open_orders(self, **params: object) -> dict[str, Any]:
        if self.raise_on_open_orders is not None:
            raise self.raise_on_open_orders
        self.last_status_params = params
        return self.open_orders_response

    def get_order_history(self, **params: object) -> dict[str, Any]:
        self.last_status_params = params
        return self.order_history_response


def _install_fake_pybit(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeHTTPSession,
) -> None:
    pybit_module = types.ModuleType("pybit")
    unified_trading_module = types.ModuleType("pybit.unified_trading")

    class HTTP:
        def __init__(self, **kwargs: object) -> None:
            session.init_kwargs = kwargs

        def __getattr__(self, name: str) -> Any:
            return getattr(session, name)

    unified_trading_module.HTTP = HTTP
    pybit_module.unified_trading = unified_trading_module
    monkeypatch.setitem(sys.modules, "pybit", pybit_module)
    monkeypatch.setitem(sys.modules, "pybit.unified_trading", unified_trading_module)


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeHTTPSession | None = None,
) -> tuple[BybitRESTClient, FakeHTTPSession]:
    fake_session = session or FakeHTTPSession()
    settings.bybit_testnet = True
    settings.bybit_testnet_api_key = "test-key"
    settings.bybit_testnet_api_secret = "test-secret"
    _install_fake_pybit(monkeypatch, fake_session)
    return BybitRESTClient(), fake_session


def test_client_init_raises_when_pybit_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "pybit", raising=False)
    monkeypatch.delitem(sys.modules, "pybit.unified_trading", raising=False)
    original_import = __import__

    def _raising_import(name: str, *args: object, **kwargs: object) -> Any:
        if name == "pybit.unified_trading":
            raise ModuleNotFoundError("no pybit")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raising_import)

    with pytest.raises(BybitConnectionError, match="pybit is not installed"):
        BybitRESTClient()


def test_get_wallet_balance_returns_unwrapped_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _build_client(monkeypatch)

    payload = client.get_wallet_balance()

    assert payload == {"list": []}
    assert session.init_kwargs == {
        "testnet": True,
        "api_key": "test-key",
        "api_secret": "test-secret",
    }


def test_get_wallet_balance_wraps_connection_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.raise_on_wallet_balance = RuntimeError("boom")
    client, _ = _build_client(monkeypatch, fake_session)

    with pytest.raises(BybitConnectionError, match="Failed to fetch wallet balance"):
        client.get_wallet_balance()


def test_get_instruments_info_returns_first_item(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch)

    item = client.get_instruments_info("BTCUSDT")

    assert item == {"symbol": "BTCUSDT"}


def test_get_instruments_info_raises_when_symbol_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.instruments_info_response = {"retCode": 0, "result": {"list": []}}
    client, _ = _build_client(monkeypatch, fake_session)

    with pytest.raises(BybitAPIError, match="Symbol 'BTCUSDT' not found"):
        client.get_instruments_info("BTCUSDT")


def test_get_klines_parses_items_into_dataclass(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch)

    klines = client.get_klines(symbol="BTCUSDT", interval="15", limit=1)

    assert klines == [
        BybitKline(
            start_time=datetime.fromtimestamp(1710000000, tz=UTC),
            open_price=1.0,
            high_price=2.0,
            low_price=0.5,
            close_price=1.5,
            volume=10.0,
            turnover=20.0,
        ),
    ]


def test_get_ticker_price_returns_last_price(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch)

    price = client.get_ticker_price("BTCUSDT")

    assert price == 123.45


def test_get_ticker_price_wraps_connection_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.raise_on_tickers = RuntimeError("boom")
    client, _ = _build_client(monkeypatch, fake_session)

    with pytest.raises(BybitConnectionError, match="Failed to fetch ticker price"):
        client.get_ticker_price("BTCUSDT")


def test_parse_kline_item_raises_on_malformed_payload() -> None:
    with pytest.raises(BybitAPIError, match="Unexpected kline payload"):
        BybitRESTClient._parse_kline_item({"not": "a-list"})


def test_place_order_builds_spot_market_protection_params(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _build_client(monkeypatch)

    payload = client.place_order(
        category="spot",
        symbol="BTCUSDT",
        side="Buy",
        order_type="Market",
        qty="1",
        order_link_id="entry-1",
        market_unit="quoteCoin",
        trigger_price="99",
        order_filter="tpslOrder",
        reduce_only=True,
    )

    assert payload == {"orderId": "abc"}
    assert session.last_place_order_params == {
        "category": "spot",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Market",
        "qty": "1",
        "orderLinkId": "entry-1",
        "marketUnit": "quoteCoin",
        "triggerPrice": "99",
        "orderFilter": "tpslOrder",
        "reduceOnly": True,
    }


def test_place_order_sets_derivatives_stop_loss_order_type(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _build_client(monkeypatch)

    client.place_order(
        category="linear",
        symbol="BTCUSDT",
        side="Buy",
        order_type="Limit",
        qty="1",
        price="100",
        sl_price="90",
        tp_price="130",
        is_post_only=True,
    )

    assert session.last_place_order_params is not None
    assert session.last_place_order_params["stopLoss"] == "90"
    assert session.last_place_order_params["slOrderType"] == "Market"
    assert session.last_place_order_params["takeProfit"] == "130"
    assert session.last_place_order_params["timeInForce"] == "PostOnly"


def test_place_order_maps_invalid_request_to_bybit_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    invalid_request_error = type("InvalidRequestError", (Exception,), {})("bad params")
    invalid_request_error.status_code = 400
    fake_session.raise_on_place_order = invalid_request_error
    client, _ = _build_client(monkeypatch, fake_session)

    with pytest.raises(BybitAPIError, match="bad params"):
        client.place_order(
            category="spot",
            symbol="BTCUSDT",
            side="Buy",
            order_type="Market",
            qty="1",
        )


def test_cancel_order_requires_identifier() -> None:
    client = object.__new__(BybitRESTClient)

    with pytest.raises(InvalidOrderParamsError, match="requires either order_id or order_link_id"):
        client.cancel_order(category="spot", symbol="BTCUSDT")


def test_cancel_order_unwraps_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client, session = _build_client(monkeypatch)

    payload = client.cancel_order(category="spot", symbol="BTCUSDT", order_link_id="entry-1")

    assert payload == {"orderId": "cancelled"}
    assert session.last_cancel_order_params == {
        "category": "spot",
        "symbol": "BTCUSDT",
        "orderLinkId": "entry-1",
    }


def test_get_order_status_prefers_open_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.open_orders_response = {"retCode": 0, "result": {"list": [{"orderId": "open-1"}]}}
    client, _ = _build_client(monkeypatch, fake_session)

    payload = client.get_order_status(category="spot", symbol="BTCUSDT", order_link_id="entry-1")

    assert payload == {"orderId": "open-1"}


def test_get_order_status_falls_back_to_history(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.order_history_response = {"retCode": 0, "result": {"list": [{"orderId": "hist-1"}]}}
    client, _ = _build_client(monkeypatch, fake_session)

    payload = client.get_order_status(category="spot", symbol="BTCUSDT", order_id="oid-1")

    assert payload == {"orderId": "hist-1"}


def test_get_order_status_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch)

    assert client.get_order_status(category="spot", symbol="BTCUSDT", order_link_id="missing") is None


def test_get_order_status_wraps_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = FakeHTTPSession()
    fake_session.raise_on_open_orders = RuntimeError("boom")
    client, _ = _build_client(monkeypatch, fake_session)

    with pytest.raises(BybitConnectionError, match="Failed to fetch order status"):
        client.get_order_status(category="spot", symbol="BTCUSDT", order_link_id="entry-1")
