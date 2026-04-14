from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Read .env directly — bypasses the os.environ["DATABASE_URL"] = "sqlite://..."
# override that tests/conftest.py applies for unit tests.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


def _testnet_db_url() -> str:
    """Returns the real PostgreSQL URL for e2e tests, reading .env directly."""
    raw: dict[str, str | None] = dotenv_values(_ENV_FILE)
    url = raw.get("DATABASE_URL") or ""
    return url if not url.startswith("sqlite") else ""


@pytest_asyncio.fixture
async def testnet_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    url = _testnet_db_url()
    if not url:
        pytest.skip(
            "Real PostgreSQL DATABASE_URL not found in .env — skipping e2e test."
        )
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
