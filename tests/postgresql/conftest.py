from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
import os

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import settings
from backend.database import get_session
from backend.main import create_app

TEST_POSTGRESQL_URL_ENV = "TEST_POSTGRESQL_URL"


class PostgresqlApiRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"},
        }

    def place_order(self, **_: object) -> dict[str, object]:
        return {"orderId": "unused"}

    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
            "orderLinkId": order_link_id,
        }

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return None


def _require_postgresql_test_url() -> str:
    database_url = os.environ.get(TEST_POSTGRESQL_URL_ENV)
    if not database_url:
        pytest.skip(
            f"{TEST_POSTGRESQL_URL_ENV} is not set. PostgreSQL Alembic integration tests are opt-in."
        )
    return database_url


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _upgrade_database(database_url: str) -> None:
    alembic_config = Config("alembic.ini")
    original_database_url = settings.database_url
    settings.database_url = database_url
    try:
        await asyncio.to_thread(command.upgrade, alembic_config, "head")
    finally:
        settings.database_url = original_database_url


@pytest.fixture
def postgresql_database_url() -> Generator[str, None, None]:
    database_url = _require_postgresql_test_url()
    original_database_url = settings.database_url
    settings.database_url = database_url
    try:
        yield database_url
    finally:
        settings.database_url = original_database_url


@pytest_asyncio.fixture
async def postgresql_session_factory(
    postgresql_database_url: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    await _reset_database(postgresql_database_url)
    await _upgrade_database(postgresql_database_url)

    engine = create_async_engine(postgresql_database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.fixture
def postgresql_client(
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> Generator[TestClient, None, None]:
    settings.trading_mode = "shadow"
    app = create_app(
        session_factory=postgresql_session_factory,
        rest_client=PostgresqlApiRestClient(),
    )

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with postgresql_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client
