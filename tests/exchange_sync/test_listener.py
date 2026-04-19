from __future__ import annotations

import sys
import types

import pytest

from backend.config import settings
from backend.exchange_sync.listener import BybitWebSocketListener


class _RecordingWebSocket:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.order_callback = None
        self.execution_callback = None
        self.exited = False

    def order_stream(self, *, callback: object) -> None:
        self.order_callback = callback

    def execution_stream(self, *, callback: object) -> None:
        self.execution_callback = callback

    def exit(self) -> None:
        self.exited = True


def _install_fake_pybit(
    monkeypatch: pytest.MonkeyPatch,
    ws_instances: list[_RecordingWebSocket],
) -> None:
    pybit_module = types.ModuleType("pybit")
    unified_trading_module = types.ModuleType("pybit.unified_trading")

    class WebSocket(_RecordingWebSocket):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            ws_instances.append(self)

    unified_trading_module.WebSocket = WebSocket
    pybit_module.unified_trading = unified_trading_module
    monkeypatch.setitem(sys.modules, "pybit", pybit_module)
    monkeypatch.setitem(sys.modules, "pybit.unified_trading", unified_trading_module)


def test_listener_routes_order_and_execution_messages() -> None:
    listener = BybitWebSocketListener()
    order_messages: list[dict[str, object]] = []
    execution_messages: list[dict[str, object]] = []
    listener.on_order(order_messages.append)
    listener.on_execution(execution_messages.append)

    listener._handle_order({"topic": "order"})
    listener._handle_execution({"topic": "execution"})

    assert order_messages == [{"topic": "order"}]
    assert execution_messages == [{"topic": "execution"}]


def test_listener_does_not_start_without_credentials() -> None:
    settings.bybit_testnet = True
    settings.bybit_testnet_api_key = ""
    settings.bybit_testnet_api_secret = ""
    listener = BybitWebSocketListener()

    listener.start()

    assert listener._ws is None


def test_listener_does_not_start_when_pybit_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings.bybit_testnet = True
    settings.bybit_testnet_api_key = "key"
    settings.bybit_testnet_api_secret = "secret"
    monkeypatch.delitem(sys.modules, "pybit", raising=False)
    monkeypatch.delitem(sys.modules, "pybit.unified_trading", raising=False)
    original_import = __import__

    def _raising_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pybit.unified_trading":
            raise ModuleNotFoundError("no pybit")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raising_import)
    listener = BybitWebSocketListener()

    listener.start()

    assert listener._ws is None


def test_listener_starts_and_stops_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    settings.bybit_testnet = True
    settings.bybit_testnet_api_key = "key"
    settings.bybit_testnet_api_secret = "secret"
    ws_instances: list[_RecordingWebSocket] = []
    _install_fake_pybit(monkeypatch, ws_instances)
    listener = BybitWebSocketListener()

    listener.start()

    assert len(ws_instances) == 1
    ws = ws_instances[0]
    assert ws.kwargs == {
        "testnet": True,
        "channel_type": "private",
        "api_key": "key",
        "api_secret": "secret",
    }
    assert ws.order_callback is not None
    assert ws.execution_callback is not None

    listener.stop()

    assert ws.exited is True
    assert listener._ws is None
