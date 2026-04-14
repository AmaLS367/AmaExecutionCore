from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.order_executor.idempotency import is_trade_terminal
from backend.order_executor.executor import OrderExecutor
from backend.signal_execution.idempotency import (
    fingerprint_signal_request,
    normalize_execute_signal_request,
)
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.trade_journal.models import SignalDirection, Trade, TradeStatus
from backend.trade_journal.store import TradeJournalStore


@dataclass(slots=True)
class ExecutionResult:
    signal_id: UUID
    trade_id: UUID
    order_link_id: str | None
    status: str
    mode: str
    replayed: bool


class ExecutionService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        order_executor: OrderExecutor,
    ) -> None:
        self._session_factory = session_factory
        self._order_executor = order_executor

    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> ExecutionResult:
        normalized_signal = normalize_execute_signal_request(signal)
        direction = SignalDirection(normalized_signal.direction)
        fingerprint = fingerprint_signal_request(normalized_signal)
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            submission = await store.get_signal_submission(fingerprint)
            if submission is not None:
                existing_trade = await store.get_trade_for_submission(submission)
                if existing_trade is not None and not is_trade_terminal(existing_trade.status):
                    replayed_trade = existing_trade
                    if existing_trade.status == TradeStatus.ORDER_PENDING_UNKNOWN:
                        replayed_trade = await self._order_executor.reconcile_pending_unknown(
                            session=session,
                            trade=existing_trade,
                        )
                        await session.commit()
                    if not is_trade_terminal(replayed_trade.status):
                        signal_id = submission.signal_id or replayed_trade.signal_id
                        if signal_id is None:
                            raise RuntimeError(
                                f"Signal submission {submission.id} is missing its signal reference."
                            )
                        return self._build_result(signal_id, replayed_trade, replayed=True)
                    # Trade resolved to terminal during reconciliation — fall through to new trade

            if submission is None:
                submission = await store.create_signal_submission(fingerprint=fingerprint)

            persisted_signal = await store.create_signal(
                symbol=normalized_signal.symbol,
                direction=direction,
                reason=normalized_signal.reason,
                strategy_version=normalized_signal.strategy_version,
                indicators_snapshot=normalized_signal.indicators_snapshot,
            )
            submission.signal_id = persisted_signal.id
            submission.trade_id = None
            await session.flush()
            trade = await self._order_executor.execute(
                session=session,
                signal_id=persisted_signal.id,
                symbol=normalized_signal.symbol,
                direction=direction,
                entry=normalized_signal.entry,
                stop=normalized_signal.stop,
                target=normalized_signal.target,
            )
            submission.trade_id = trade.id
            await session.commit()
            return self._build_result(persisted_signal.id, trade, replayed=False)

    @staticmethod
    def _build_result(signal_id: UUID, trade: Trade, *, replayed: bool) -> ExecutionResult:
        return ExecutionResult(
            signal_id=signal_id,
            trade_id=trade.id,
            order_link_id=trade.order_link_id,
            status=trade.status.value,
            mode=trade.mode.value,
            replayed=replayed,
        )
