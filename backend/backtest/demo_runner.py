from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.trade_journal.models import Trade, TradeStatus


class DemoRunner:
    def __init__(
        self,
        *,
        execution_service: Any,
        position_manager: Any,
        session_factory: async_sessionmaker[AsyncSession],
        sync_engine: Any | None = None,
    ) -> None:
        self._execution_service = execution_service
        self._position_manager = position_manager
        self._session_factory = session_factory
        self._sync_engine = sync_engine

    async def execute_and_close(self, *, signal: Any) -> Trade:
        result = await self._execution_service.execute_signal(signal=signal)
        trade = await self._wait_for_trade(
            trade_id=result.trade_id,
            accepted_statuses={TradeStatus.POSITION_OPEN, TradeStatus.ORDER_PARTIALLY_FILLED},
            timeout_seconds=settings.demo_close_ttl_seconds,
        )
        await asyncio.sleep(settings.demo_close_ttl_seconds)
        await self._position_manager.close_trade(trade_id=trade.id)
        return await self._wait_for_trade(
            trade_id=trade.id,
            accepted_statuses={TradeStatus.PNL_RECORDED, TradeStatus.POSITION_CLOSE_FAILED},
            timeout_seconds=settings.demo_close_ttl_seconds,
        )

    async def _wait_for_trade(
        self,
        *,
        trade_id: UUID,
        accepted_statuses: set[TradeStatus],
        timeout_seconds: int,
    ) -> Trade:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._sync_engine is not None:
                await self._sync_engine.reconcile_once()
            async with self._session_factory() as session:
                trade = (
                    await session.execute(select(Trade).where(Trade.id == trade_id))
                ).scalar_one()
                if trade.status in accepted_statuses:
                    return trade
            await asyncio.sleep(settings.demo_poll_interval_seconds)
        raise TimeoutError(f"Trade {trade_id} did not reach statuses {accepted_statuses}.")
