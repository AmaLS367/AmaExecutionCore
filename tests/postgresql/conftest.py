from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import os

from alembic import command
from alembic.config import Config
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings

TEST_POSTGRESQL_URL_ENV = "TEST_POSTGRESQL_URL"


def _require_postgresql_test_url() -> str:
    database_url = os.environ.get(TEST_POSTGRESQL_URL_ENV)
    if not database_url:
        pytest.skip(
            f"{TEST_POSTGRESQL_URL_ENV} is not set. PostgreSQL Alembic integration tests are opt-in."
        )
    return database_url


def _reset_database(database_url: str) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
            await engine.dispose()
        except Exception:
            await engine.dispose()
            raise

    import asyncio

    asyncio.run(run())


def _upgrade_database(database_url: str) -> None:
    alembic_config = Config("alembic.ini")
    original_database_url = settings.database_url
    settings.database_url = database_url
    try:
        command.upgrade(alembic_config, "head")
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
    _reset_database(postgresql_database_url)
    _upgrade_database(postgresql_database_url)

    engine = create_async_engine(postgresql_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()
