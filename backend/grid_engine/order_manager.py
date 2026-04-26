from __future__ import annotations

from typing import Any, Protocol

from loguru import logger


class GridOrderRESTClient(Protocol):
    def place_order(
        self,
        *,
        category: str,
        symbol: str,
        side: str,
        order_type: str,
        qty: str,
        price: str | None = None,
        order_link_id: str | None = None,
        is_post_only: bool = False,
    ) -> dict[str, Any]:
        ...

    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_open_orders(self, *, category: str, symbol: str) -> list[dict[str, Any]]:
        ...


class GridOrderManager:
    def __init__(self, rest_client: GridOrderRESTClient) -> None:
        self._rest_client = rest_client

    def place_buy_limit(self, symbol: str, price: float, qty: float) -> str:
        return self._place_limit(symbol=symbol, side="Buy", price=price, qty=qty)

    def place_sell_limit(self, symbol: str, price: float, qty: float) -> str:
        return self._place_limit(symbol=symbol, side="Sell", price=price, qty=qty)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        logger.info("Cancelling grid order. symbol={} order_id={}", symbol, order_id)
        self._rest_client.cancel_order(category="spot", symbol=symbol, order_id=order_id)
        return True

    def cancel_all_orders(self, symbol: str) -> int:
        open_orders = self.get_open_orders(symbol)
        cancelled = 0
        for order in open_orders:
            order_id = _order_id(order)
            if order_id is None:
                continue
            if self.cancel_order(symbol, order_id):
                cancelled += 1
        return cancelled

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self._rest_client.get_open_orders(category="spot", symbol=symbol)

    def _place_limit(self, *, symbol: str, side: str, price: float, qty: float) -> str:
        logger.info(
            "Placing grid limit order. symbol={} side={} price={} qty={}",
            symbol,
            side,
            price,
            qty,
        )
        result = self._rest_client.place_order(
            category="spot",
            symbol=symbol,
            side=side,
            order_type="Limit",
            qty=_format_qty(qty),
            price=_format_price(price),
            is_post_only=True,
        )
        order_id = _order_id(result)
        if order_id is None:
            raise ValueError(f"Grid order placement response missing order id: {result!r}")
        return order_id


def _price_decimals(price: float) -> int:
    """Estimate tick-size precision from price magnitude.

    Bybit spot typical tick sizes:
    BTC/ETH/BNB (>=100 USDT)  → 0.01  (2 dp)
    SOL/AVAX    (10-100 USDT) -> 0.01  (2 dp)
    XRP/ADA     (1-10 USDT)  -> 0.0001 (4 dp)
    DOGE/SHIB   (<1 USDT)     → 0.00001 (5 dp)
    """
    if price >= 10:
        return 2
    if price >= 1:
        return 4
    return 5


def _format_price(price: float) -> str:
    return f"{price:.{_price_decimals(price)}f}"


def _format_qty(qty: float) -> str:
    return f"{qty:.8f}".rstrip("0").rstrip(".")


def _order_id(payload: dict[str, Any]) -> str | None:
    raw_order_id = payload.get("orderId") or payload.get("order_id")
    if raw_order_id is None:
        return None
    return str(raw_order_id)
