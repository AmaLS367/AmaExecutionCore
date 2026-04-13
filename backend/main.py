from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.exchange_sync.listener import ws_listener
from backend.safety_guard.router import router as safety_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    sync_engine = ExchangeSyncEngine(session_factory=AsyncSessionLocal)
    ws_listener.start()
    sync_engine.wire(ws_listener)
    yield
    ws_listener.stop()


app = FastAPI(
    title="AmaExecutionCore API",
    version="0.1.0",
    description="Trading Bot execution core built on strict Risk Management rules.",
    lifespan=lifespan,
)

app.include_router(safety_router)


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
