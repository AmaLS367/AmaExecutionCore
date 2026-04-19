from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings

# Read .env directly — bypasses the os.environ["DATABASE_URL"] = "sqlite://..."
# override that tests/conftest.py applies for unit tests.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


def _testnet_db_url() -> str:
    """Returns the real PostgreSQL URL for e2e tests, reading .env directly."""
    raw: dict[str, str | None] = dotenv_values(_ENV_FILE)
    url = raw.get("DATABASE_URL") or ""
    return url if not url.startswith("sqlite") else ""


async def _upgrade_database(database_url: str) -> None:
    alembic_config = Config("alembic.ini")
    original_database_url = settings.database_url
    settings.database_url = database_url
    try:
        await asyncio.to_thread(command.upgrade, alembic_config, "head")
    finally:
        settings.database_url = original_database_url


@pytest_asyncio.fixture
async def testnet_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    url = _testnet_db_url()
    if not url:
        pytest.skip(
            "Real PostgreSQL DATABASE_URL not found in .env — skipping e2e test."
        )
    await _upgrade_database(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
