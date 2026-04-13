from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.order_executor.executor import OrderExecutor
from backend.trade_journal.models import SignalDirection, Trade
from backend.trade_journal.store import TradeJournalStore


@dataclass(slots=True)
class ExecutionResult:
    signal_id: UUID
    trade_id: UUID
    order_link_id: str | None
    status: str
    mode: str


class ExecutionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        order_executor: OrderExecutor,
    ) -> None:
        self._session_factory = session_factory
        self._order_executor = order_executor

    async def execute_signal(self, *, signal: Any) -> ExecutionResult:
        direction = SignalDirection(signal.direction)
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            persisted_signal = await store.create_signal(
                symbol=signal.symbol,
                direction=direction,
                reason=getattr(signal, "reason", None),
                strategy_version=getattr(signal, "strategy_version", None),
                indicators_snapshot=getattr(signal, "indicators_snapshot", None),
            )
            trade = await self._order_executor.execute(
                session=session,
                signal_id=persisted_signal.id,
                symbol=signal.symbol,
                direction=direction,
                entry=signal.entry,
                stop=signal.stop,
                target=signal.target,
            )
            return self._build_result(persisted_signal.id, trade)

    @staticmethod
    def _build_result(signal_id: UUID, trade: Trade) -> ExecutionResult:
        return ExecutionResult(
            signal_id=signal_id,
            trade_id=trade.id,
            order_link_id=trade.order_link_id,
            status=trade.status.value,
            mode=trade.mode.value,
        )
