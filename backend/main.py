from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.exchange_sync.listener import ws_listener
from backend.position_manager.router import router as position_router
from backend.position_manager.service import PositionManagerService
from backend.safety_guard.router import router as safety_router
from backend.signal_execution.router import router as signal_router
from backend.signal_execution.service import ExecutionService
from backend.order_executor.executor import OrderExecutor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    sync_engine = ExchangeSyncEngine(
        session_factory=app.state.session_factory,
        rest_client=app.state.rest_client,
    )
    app.state.exchange_sync = sync_engine
    ws_listener.start()
    sync_engine.wire(ws_listener)
    sync_engine.start_reconciliation_worker()
    yield
    await sync_engine.stop_reconciliation_worker()
    ws_listener.stop()

class NullRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def place_order(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def cancel_order(self, **_: object) -> dict[str, object]:
        raise RuntimeError("Bybit REST client is not available.")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        raise RuntimeError("Bybit REST client is not available.")


def create_app(
    *,
    session_factory: Any = AsyncSessionLocal,
    rest_client: Any | None = None,
) -> FastAPI:
    if rest_client is None:
        rest_client = BybitRESTClient()

    order_executor = OrderExecutor(rest_client=rest_client)
    execution_service = ExecutionService(
        session_factory=session_factory,
        order_executor=order_executor,
    )
    position_manager = PositionManagerService(
        session_factory=session_factory,
        rest_client=rest_client,
    )

    app = FastAPI(
        title="AmaExecutionCore API",
        version="0.1.0",
        description="Trading Bot execution core built on strict Risk Management rules.",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.rest_client = rest_client
    app.state.execution_service = execution_service
    app.state.position_manager = position_manager
    app.include_router(safety_router)
    app.include_router(signal_router)
    app.include_router(position_router)
    return app


app = create_app()


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Basic health check to verify the app is running and config is loaded."""
    obfuscated_key = (
        f"{settings.bybit_api_key[:4]}***" if len(settings.bybit_api_key) > 4 else "Not Set"
    )
    return {
        "status": "ok",
        "trading_mode": settings.trading_mode,
        "environment": settings.environment,
        "bybit_testnet": settings.bybit_testnet,
        "api_key_status": obfuscated_key,
    }
