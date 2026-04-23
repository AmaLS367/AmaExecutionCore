import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.trade_journal.models import Trade, TradeStatus

_TERMINAL_STATUSES = {
    TradeStatus.ORDER_REJECTED,
    TradeStatus.ORDER_CANCELLED,
    TradeStatus.POSITION_CLOSED,
    TradeStatus.POSITION_CLOSE_FAILED,
    TradeStatus.PNL_RECORDED,
}


def generate_order_link_id(signal_id: str) -> str:
    """
    Generates a unique orderLinkId tied to a signal.
    Each call produces a NEW id — never reuse on retry.
    """
    return f"{signal_id}_{uuid.uuid4().hex[:8]}"


def is_trade_terminal(status: TradeStatus) -> bool:
    return status in _TERMINAL_STATUSES


async def is_order_already_submitted(
    session: AsyncSession, signal_id: uuid.UUID,
) -> bool:
    """
    Returns True if a non-terminal trade already exists for this signal.
    Prevents double-submission after restart or network timeout uncertainty.
    """
    stmt = select(
        exists().where(
            Trade.signal_id == signal_id,
            Trade.status.not_in(list(_TERMINAL_STATUSES)),
        ),
    )
    result = await session.execute(stmt)
    return bool(result.scalar())
