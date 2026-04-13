from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["LOG_LEVEL"] = "INFO"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from backend.config import settings
from backend.database import Base


@pytest.fixture(autouse=True)
def reset_settings() -> Generator[None, None, None]:
    original_values = {
        "database_url": settings.database_url,
        "trading_mode": settings.trading_mode,
        "order_mode": settings.order_mode,
        "shadow_equity": getattr(settings, "shadow_equity", 10_000.0),
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "min_rrr": settings.min_rrr,
        "max_open_positions": settings.max_open_positions,
        "max_total_risk_exposure_pct": settings.max_total_risk_exposure_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_weekly_loss_pct": settings.max_weekly_loss_pct,
        "max_consecutive_losses": settings.max_consecutive_losses,
        "cooldown_hours": settings.cooldown_hours,
        "demo_close_ttl_seconds": getattr(settings, "demo_close_ttl_seconds", 30),
    }
    yield
    for field_name, field_value in original_values.items():
        setattr(settings, field_name, field_value)


@pytest_asyncio.fixture
async def sqlite_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()
