from collections.abc import Callable
from typing import Any

from loguru import logger

from backend.config import settings


class BybitWebSocketListener:
    """
    Manages the Bybit private WebSocket connection.

    Subscribes to 'order' and 'execution' topics and routes incoming
    messages to registered handlers. Handlers are registered per-topic
    and called in pybit's internal background thread.

    Actual state transitions (DB writes) are wired in Stage 4
    (Exchange Sync Engine). At this stage handlers just log.
    """

    def __init__(self) -> None:
        self._ws: Any | None = None
        self._order_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._execution_handlers: list[Callable[[dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def on_order(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback for 'order' topic events."""
        self._order_handlers.append(handler)

    def on_execution(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback for 'execution' topic events."""
        self._execution_handlers.append(handler)

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    def _handle_order(self, message: dict[str, Any]) -> None:
        logger.debug("WS [order] event: {}", message)
        for handler in self._order_handlers:
            handler(message)

    def _handle_execution(self, message: dict[str, Any]) -> None:
        logger.debug("WS [execution] event: {}", message)
        for handler in self._execution_handlers:
            handler(message)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Connects to Bybit private WebSocket and subscribes to order/execution topics.
        Skipped silently when API credentials are absent (shadow mode, tests).
        Pybit handles reconnection internally.
        """
        if not settings.active_api_key or not settings.active_api_secret:
            logger.warning(
                "Bybit API credentials not set — WebSocket listener not started.",
            )
            return

        logger.info(
            "Starting Bybit WebSocket listener. testnet={}", settings.bybit_testnet,
        )
        try:
            from pybit.unified_trading import WebSocket  # type: ignore[import-untyped]
        except ModuleNotFoundError:
            logger.warning("pybit is not installed — WebSocket listener not started.")
            return

        self._ws = WebSocket(
            testnet=settings.bybit_testnet,
            channel_type="private",
            api_key=settings.active_api_key,
            api_secret=settings.active_api_secret,
        )
        self._ws.order_stream(callback=self._handle_order)
        self._ws.execution_stream(callback=self._handle_execution)
        logger.info("WebSocket listener started. Subscribed to: order, execution")

    def stop(self) -> None:
        """Closes the WebSocket connection on app shutdown."""
        if self._ws is not None:
            logger.info("Stopping Bybit WebSocket listener.")
            self._ws.exit()
            self._ws = None


ws_listener = BybitWebSocketListener()
