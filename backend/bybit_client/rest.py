from typing import Any

from loguru import logger
from pybit.unified_trading import HTTP  # type: ignore[import-not-found]

from backend.bybit_client.exceptions import (
    BybitAPIError,
    BybitConnectionError,
    InvalidOrderParamsError,
)
from backend.config import settings


class BybitRESTClient:
    """
    Thin facade over pybit V5 HTTP session.

    Responsibilities:
    - Authenticate and hold the HTTP session.
    - Unwrap Bybit responses and raise domain exceptions on errors.
    - Expose only the endpoints needed by the execution pipeline.

    Note: pybit HTTP is synchronous. Async callers must bridge via
    asyncio.to_thread() or anyio.to_thread.run_sync().
    """

    def __init__(self) -> None:
        self._session: HTTP = HTTP(
            testnet=settings.bybit_testnet,
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_api_secret,
        )

    def _unwrap(self, response: dict[str, Any]) -> dict[str, Any]:
        """Validates Bybit response retCode and returns the result payload."""
        ret_code: int = response.get("retCode", -1)
        if ret_code != 0:
            raise BybitAPIError(
                ret_code=ret_code,
                ret_msg=response.get("retMsg", "Unknown error"),
            )
        result: dict[str, Any] = response.get("result", {})
        return result

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any]:
        """
        Returns wallet balance for the given account type.
        account_type: UNIFIED | CONTRACT | SPOT
        """
        logger.debug("Fetching wallet balance. account_type={}", account_type)
        try:
            response: dict[str, Any] = self._session.get_wallet_balance(
                accountType=account_type
            )
        except Exception as exc:
            raise BybitConnectionError(
                f"Failed to fetch wallet balance: {exc}"
            ) from exc
        return self._unwrap(response)

    # ------------------------------------------------------------------
    # Market info
    # ------------------------------------------------------------------

    def get_instruments_info(
        self, symbol: str, category: str = "spot"
    ) -> dict[str, Any]:
        """
        Returns instrument info for a single symbol (lotSizeFilter, priceFilter…).
        Required by apply_exchange_constraints before position sizing.
        """
        logger.debug(
            "Fetching instruments info. symbol={} category={}", symbol, category
        )
        try:
            response: dict[str, Any] = self._session.get_instruments_info(
                category=category, symbol=symbol
            )
        except Exception as exc:
            raise BybitConnectionError(
                f"Failed to fetch instruments info for {symbol}: {exc}"
            ) from exc
        result = self._unwrap(response)
        items: list[dict[str, Any]] = result.get("list", [])
        if not items:
            raise BybitAPIError(
                ret_code=0,
                ret_msg=f"Symbol '{symbol}' not found in category '{category}'",
            )
        return items[0]

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

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
        sl_price: str | None = None,
        tp_price: str | None = None,
        market_unit: str | None = None,
    ) -> dict[str, Any]:
        """
        Places an order on Bybit. All numeric values must be pre-formatted strings.

        sl_price is always submitted as a Market stop — per project rules SL = market.
        market_unit must be set explicitly for spot market orders (baseCoin | quoteCoin).
        """
        params: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
        }
        if price is not None:
            params["price"] = price
        if order_link_id is not None:
            params["orderLinkId"] = order_link_id
        if is_post_only:
            params["timeInForce"] = "PostOnly"
        if sl_price is not None:
            params["stopLoss"] = sl_price
            params["slOrderType"] = "Market"
        if tp_price is not None:
            params["takeProfit"] = tp_price
        if market_unit is not None:
            params["marketUnit"] = market_unit

        logger.info(
            "Placing order. symbol={} side={} type={} qty={} order_link_id={}",
            symbol,
            side,
            order_type,
            qty,
            order_link_id,
        )
        try:
            response: dict[str, Any] = self._session.place_order(**params)
        except Exception as exc:
            raise BybitConnectionError(f"Failed to place order: {exc}") from exc
        return self._unwrap(response)

    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancels an open order. Requires either order_id or order_link_id."""
        if order_id is None and order_link_id is None:
            raise InvalidOrderParamsError(
                "cancel_order requires either order_id or order_link_id."
            )
        params: dict[str, Any] = {"category": category, "symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if order_link_id is not None:
            params["orderLinkId"] = order_link_id

        logger.info(
            "Cancelling order. symbol={} order_id={} order_link_id={}",
            symbol,
            order_id,
            order_link_id,
        )
        try:
            response: dict[str, Any] = self._session.cancel_order(**params)
        except Exception as exc:
            raise BybitConnectionError(f"Failed to cancel order: {exc}") from exc
        return self._unwrap(response)

    def get_order_status(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Checks open orders first, then history. Returns None if not found in either.
        Used for idempotency resolution after timeout / network uncertainty.
        """
        if order_id is None and order_link_id is None:
            raise InvalidOrderParamsError(
                "get_order_status requires either order_id or order_link_id."
            )
        params: dict[str, Any] = {"category": category, "symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if order_link_id is not None:
            params["orderLinkId"] = order_link_id

        logger.debug(
            "Fetching order status. symbol={} order_link_id={}", symbol, order_link_id
        )
        try:
            open_result = self._unwrap(self._session.get_open_orders(**params))
            open_items: list[dict[str, Any]] = open_result.get("list", [])
            if open_items:
                return open_items[0]

            history_result = self._unwrap(self._session.get_order_history(**params))
            history_items: list[dict[str, Any]] = history_result.get("list", [])
            return history_items[0] if history_items else None

        except BybitAPIError:
            raise
        except Exception as exc:
            raise BybitConnectionError(
                f"Failed to fetch order status: {exc}"
            ) from exc
