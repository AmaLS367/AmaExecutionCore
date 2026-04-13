from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.trade_journal.models import (
    PauseReason,
    SafetyState,
    Signal,
    SignalDirection,
    SystemEvent,
    SystemEventType,
    Trade,
)


@dataclass(slots=True)
class PersistedSafetyState:
    kill_switch_active: bool
    pause_reason: PauseReason | None
    cooldown_until: datetime | None
    manual_reset_required: bool


class TradeJournalStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_signal(
        self,
        *,
        symbol: str,
        direction: SignalDirection,
        reason: str | None,
        strategy_version: str | None,
        indicators_snapshot: dict[str, object] | None,
    ) -> Signal:
        signal = Signal(
            symbol=symbol,
            signal_direction=direction,
            reason=reason,
            strategy_version=strategy_version,
            indicators_snapshot=indicators_snapshot,
        )
        self._session.add(signal)
        await self._session.flush()
        return signal

    async def get_trade_by_order_link_id(self, order_link_id: str) -> Trade | None:
        result = await self._session.execute(
            select(Trade).where(
                or_(
                    Trade.order_link_id == order_link_id,
                    Trade.close_order_link_id == order_link_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_trade(self, trade_id: uuid.UUID) -> Trade | None:
        result = await self._session.execute(select(Trade).where(Trade.id == trade_id))
        return result.scalar_one_or_none()

    async def get_or_create_safety_state(self) -> SafetyState:
        result = await self._session.execute(select(SafetyState).where(SafetyState.id == 1))
        state = result.scalar_one_or_none()
        if state is None:
            state = SafetyState(id=1)
            self._session.add(state)
            await self._session.flush()
        return state

    async def read_safety_state(self) -> PersistedSafetyState:
        state = await self.get_or_create_safety_state()
        return PersistedSafetyState(
            kill_switch_active=state.kill_switch_active,
            pause_reason=state.pause_reason,
            cooldown_until=state.cooldown_until,
            manual_reset_required=state.manual_reset_required,
        )

    async def activate_kill_switch(self) -> SafetyState:
        state = await self.get_or_create_safety_state()
        state.kill_switch_active = True
        state.last_triggered_at = datetime.now(UTC)
        return state

    async def set_pause(
        self,
        *,
        pause_reason: PauseReason,
        manual_reset_required: bool,
        cooldown_until: datetime | None = None,
    ) -> SafetyState:
        state = await self.get_or_create_safety_state()
        state.pause_reason = pause_reason
        state.manual_reset_required = manual_reset_required
        state.cooldown_until = cooldown_until
        state.last_triggered_at = datetime.now(UTC)
        return state

    async def clear_pause_if_expired(self) -> SafetyState:
        state = await self.get_or_create_safety_state()
        cooldown_until = state.cooldown_until
        if cooldown_until is not None and cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=UTC)
        if (
            state.pause_reason == PauseReason.COOLDOWN
            and cooldown_until is not None
            and cooldown_until <= datetime.now(UTC)
        ):
            state.pause_reason = None
            state.cooldown_until = None
            state.manual_reset_required = False
        return state

    async def reset_safety_state(self) -> SafetyState:
        state = await self.get_or_create_safety_state()
        state.kill_switch_active = False
        state.pause_reason = None
        state.cooldown_until = None
        state.manual_reset_required = False
        return state

    async def append_system_event(
        self,
        *,
        event_type: SystemEventType,
        description: str,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        self._session.add(
            SystemEvent(
                event_type=event_type,
                description=description,
                event_metadata=event_metadata,
            )
        )

    @staticmethod
    def calculate_realized_pnl(trade: Trade, exit_price: Decimal) -> Decimal:
        entry_price = trade.avg_fill_price or trade.entry_price or Decimal("0")
        filled_qty = trade.filled_qty or trade.qty or Decimal("0")
        if trade.signal_direction == SignalDirection.LONG:
            return (exit_price - entry_price) * filled_qty
        return (entry_price - exit_price) * filled_qty

    @staticmethod
    def calculate_pnl_pct(trade: Trade, realized_pnl: Decimal) -> Decimal | None:
        equity_at_entry = trade.equity_at_entry
        if equity_at_entry in (None, Decimal("0")):
            return None
        assert equity_at_entry is not None
        return realized_pnl / equity_at_entry

    @staticmethod
    def calculate_pnl_in_r(trade: Trade, realized_pnl: Decimal) -> Decimal | None:
        risk_amount = trade.risk_amount_usd
        if risk_amount in (None, Decimal("0")):
            return None
        assert risk_amount is not None
        return realized_pnl / risk_amount
