from fastapi import FastAPI
from backend.config import settings

app = FastAPI(
    title="AmaExecutionCore API",
    version="0.1.0",
    description="Trading Bot execution core built on strict Risk Management rules.",
)

@app.get("/health")
async def health_check():
    """
    Basic health check to verify the app is running and config is loaded.
    """
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
