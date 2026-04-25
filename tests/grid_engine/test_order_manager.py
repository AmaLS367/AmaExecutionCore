from __future__ import annotations

from typing import Any

from backend.grid_engine.order_manager import GridOrderManager


class FakeGridRestClient:
    def __init__(self) -> None:
        self.place_order_calls: list[dict[str, Any]] = []
        self.cancel_order_calls: list[dict[str, Any]] = []
        self.open_orders = [{"orderId": "open-1"}, {"orderId": "open-2"}]

    def place_order(self, **kwargs: Any) -> dict[str, Any]:
        self.place_order_calls.append(dict(kwargs))
        return {"orderId": "order-1"}

    def cancel_order(self, **kwargs: Any) -> dict[str, Any]:
        self.cancel_order_calls.append(dict(kwargs))
        return {"orderId": kwargs["order_id"]}

    def get_open_orders(self, **_: Any) -> list[dict[str, Any]]:
        return self.open_orders


def test_place_buy_limit_calls_rest_client_with_correct_params() -> None:
    rest_client = FakeGridRestClient()
    manager = GridOrderManager(rest_client)

    order_id = manager.place_buy_limit("XRPUSDT", price=1.8, qty=2.5)

    assert order_id == "order-1"
    assert rest_client.place_order_calls == [
        {
            "category": "spot",
            "symbol": "XRPUSDT",
            "side": "Buy",
            "order_type": "Limit",
            "qty": "2.5",
            "price": "1.8",
            "is_post_only": True,
        },
    ]


def test_cancel_all_orders_cancels_each_open_order() -> None:
    rest_client = FakeGridRestClient()
    manager = GridOrderManager(rest_client)

    cancelled = manager.cancel_all_orders("XRPUSDT")

    assert cancelled == 2
    assert rest_client.cancel_order_calls == [
        {"category": "spot", "symbol": "XRPUSDT", "order_id": "open-1"},
        {"category": "spot", "symbol": "XRPUSDT", "order_id": "open-2"},
    ]
