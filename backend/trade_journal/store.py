from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.trade_journal.models import (
    DailyStat,
    MarketType,
    PauseReason,
    SafetyState,
    Signal,
    SignalDirection,
    SignalSubmission,
    SystemEvent,
    SystemEventType,
    Trade,
    TradeEvent,
    TradeStatus,
)

TRADE_CREATED_EVENT = "trade_created"
STATUS_TRANSITION_EVENT = "status_transition"


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

    async def get_signal_submission(self, fingerprint: str) -> SignalSubmission | None:
        result = await self._session.execute(
            select(SignalSubmission).where(SignalSubmission.fingerprint == fingerprint)
        )
        return result.scalar_one_or_none()

    async def create_signal_submission(self, *, fingerprint: str) -> SignalSubmission:
        submission = SignalSubmission(fingerprint=fingerprint)
        self._session.add(submission)
        await self._session.flush()
        return submission

    async def get_trade_for_submission(self, submission: SignalSubmission) -> Trade | None:
        if submission.trade_id is not None:
            trade = await self.get_trade(submission.trade_id)
            if trade is not None:
                return trade

        if submission.signal_id is None:
            return None

        result = await self._session.execute(
            select(Trade)
            .where(Trade.signal_id == submission.signal_id)
            .order_by(Trade.created_at.desc())
        )
        return result.scalars().first()

    async def get_trade_by_order_link_id(
        self,
        order_link_id: str,
        *,
        for_update: bool = False,
    ) -> Trade | None:
        stmt = select(Trade).where(
            or_(
                Trade.order_link_id == order_link_id,
                Trade.close_order_link_id == order_link_id,
                Trade.stop_order_link_id == order_link_id,
                Trade.take_profit_order_link_id == order_link_id,
            )
        )
        if for_update and self._supports_for_update():
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_trade(self, trade_id: uuid.UUID) -> Trade | None:
        result = await self._session.execute(select(Trade).where(Trade.id == trade_id))
        return result.scalar_one_or_none()

    async def get_or_create_daily_stat(self, *, stat_date: date) -> DailyStat:
        await self.ensure_daily_stat(stat_date=stat_date)
        result = await self._session.execute(select(DailyStat).where(DailyStat.stat_date == stat_date))
        return result.scalar_one()

    async def ensure_daily_stat(self, *, stat_date: date) -> None:
        dialect_name = self._dialect_name()
        if dialect_name == "postgresql":
            postgres_stmt = postgresql_insert(DailyStat).values(date=stat_date).on_conflict_do_nothing(
                index_elements=["date"]
            )
            await self._session.execute(postgres_stmt)
            await self._session.flush()
            return
        if dialect_name == "sqlite":
            sqlite_stmt = sqlite_insert(DailyStat).values(date=stat_date).on_conflict_do_nothing(
                index_elements=["date"]
            )
            await self._session.execute(sqlite_stmt)
            await self._session.flush()
            return

        result = await self._session.execute(select(DailyStat.id).where(DailyStat.stat_date == stat_date))
        if result.scalar_one_or_none() is None:
            self._session.add(DailyStat(stat_date=stat_date))
            await self._session.flush()

    async def append_trade_event(
        self,
        *,
        trade: Trade,
        event_type: str,
        from_status: TradeStatus | None,
        to_status: TradeStatus | None,
        event_metadata: dict[str, object] | None = None,
    ) -> TradeEvent:
        event = TradeEvent(
            trade_id=trade.id,
            event_type=event_type,
            from_status=from_status.value if from_status is not None else None,
            to_status=to_status.value if to_status is not None else None,
            event_metadata=event_metadata,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def record_trade_created(
        self,
        trade: Trade,
        *,
        event_metadata: dict[str, object] | None = None,
    ) -> TradeEvent:
        return await self.append_trade_event(
            trade=trade,
            event_type=TRADE_CREATED_EVENT,
            from_status=None,
            to_status=trade.status,
            event_metadata=event_metadata,
        )

    async def transition_trade_status(
        self,
        trade: Trade,
        new_status: TradeStatus,
        *,
        event_metadata: dict[str, object] | None = None,
    ) -> bool:
        previous_status = trade.status
        if previous_status == new_status:
            return False

        trade.status = new_status
        await self.append_trade_event(
            trade=trade,
            event_type=STATUS_TRANSITION_EVENT,
            from_status=previous_status,
            to_status=new_status,
            event_metadata=event_metadata,
        )
        return True

    async def apply_trade_outcome_analytics(self, trade: Trade) -> DailyStat:
        if trade.realized_pnl is None:
            raise ValueError("Trade outcome analytics require realized_pnl.")

        closed_at = trade.closed_at or datetime.now(UTC)
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=UTC)
        stat = await self.get_or_create_daily_stat(stat_date=closed_at.date())

        realized_pnl = trade.realized_pnl
        fee_paid = trade.fee_paid or Decimal("0")
        net_pnl = realized_pnl - fee_paid

        closed_trade_count = (stat.winning_trades or 0) + (stat.losing_trades or 0)
        if (stat.total_trades or 0) <= closed_trade_count:
            stat.total_trades = (stat.total_trades or 0) + 1
        stat.gross_pnl = (stat.gross_pnl or Decimal("0")) + realized_pnl
        stat.total_fees = (stat.total_fees or Decimal("0")) + fee_paid
        stat.net_pnl = (stat.net_pnl or Decimal("0")) + net_pnl

        if realized_pnl < 0:
            stat.losing_trades = (stat.losing_trades or 0) + 1
            stat.consecutive_losses = (stat.consecutive_losses or 0) + 1
            if trade.pnl_pct is not None:
                stat.daily_loss_pct = (stat.daily_loss_pct or Decimal("0")) + abs(trade.pnl_pct)
            self._update_symbol_stats(trade.symbol, stat, is_win=False)
        else:
            stat.winning_trades = (stat.winning_trades or 0) + 1
            stat.consecutive_losses = 0
            self._update_symbol_stats(trade.symbol, stat, is_win=True)

        await self._session.flush()
        return stat

    async def get_or_create_today_daily_stat(self) -> DailyStat:
        return await self.get_or_create_daily_stat(stat_date=date.today())

    @staticmethod
    def symbol_consecutive_losses(stat: DailyStat, symbol: str) -> int:
        symbol_stats = stat.symbol_stats or {}
        raw_symbol_stats = symbol_stats.get(symbol, {})
        if not isinstance(raw_symbol_stats, dict):
            return 0
        consecutive_losses = raw_symbol_stats.get("consecutive_losses", 0)
        if not isinstance(consecutive_losses, int):
            return 0
        return consecutive_losses

    async def list_trades_by_status(self, statuses: Collection[TradeStatus]) -> list[Trade]:
        if not statuses:
            return []
        result = await self._session.execute(
            select(Trade)
            .where(Trade.status.in_(tuple(statuses)))
            .order_by(Trade.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_spot_market_trades_missing_protection(self) -> list[Trade]:
        result = await self._session.execute(
            select(Trade)
            .where(
                Trade.market_type == MarketType.SPOT,
                Trade.order_type == "Market",
                Trade.status.in_((TradeStatus.ORDER_CONFIRMED, TradeStatus.POSITION_OPEN)),
            )
        )
        trades = list(result.scalars().all())
        return [
            trade
            for trade in trades
            if trade.market_type.value == "spot"
            and trade.order_type == "Market"
            and trade.status in {TradeStatus.ORDER_CONFIRMED, TradeStatus.POSITION_OPEN}
            and trade.filled_qty not in (None, Decimal("0"))
            and (
                trade.stop_order_link_id is None
                or (
                    trade.target_price is not None
                    and trade.take_profit_order_link_id is None
                )
            )
        ]

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
            await self.reset_consecutive_losses(
                stat_date=self._loss_streak_stat_date(state),
            )
            state.pause_reason = None
            state.cooldown_until = None
            state.manual_reset_required = False
        return state

    async def reset_safety_state(self, *, reset_consecutive_losses: bool = False) -> SafetyState:
        state = await self.get_or_create_safety_state()
        if reset_consecutive_losses:
            await self.reset_consecutive_losses(
                stat_date=self._loss_streak_stat_date(state),
            )
        state.kill_switch_active = False
        state.pause_reason = None
        state.cooldown_until = None
        state.manual_reset_required = False
        return state

    async def reset_consecutive_losses(self, *, stat_date: date) -> DailyStat:
        stat = await self.get_or_create_daily_stat(stat_date=stat_date)
        stat.consecutive_losses = 0
        await self._session.flush()
        return stat

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

    async def add_execution_fee(self, *, order_link_id: str, fee: Decimal) -> bool:
        trade_exists = await self._session.execute(
            select(Trade.id).where(
                or_(
                    Trade.order_link_id == order_link_id,
                    Trade.close_order_link_id == order_link_id,
                    Trade.stop_order_link_id == order_link_id,
                    Trade.take_profit_order_link_id == order_link_id,
                )
            )
        )
        if trade_exists.scalar_one_or_none() is None:
            return False

        result = await self._session.execute(
            update(Trade)
            .where(
                or_(
                    Trade.order_link_id == order_link_id,
                    Trade.close_order_link_id == order_link_id,
                    Trade.stop_order_link_id == order_link_id,
                    Trade.take_profit_order_link_id == order_link_id,
                )
            )
            .values(fee_paid=func.coalesce(Trade.fee_paid, Decimal("0")) + fee)
        )
        return result is not None

    async def increment_daily_trade_count(self, *, stat_date: date) -> DailyStat:
        await self.ensure_daily_stat(stat_date=stat_date)
        await self._session.execute(
            update(DailyStat)
            .where(DailyStat.stat_date == stat_date)
            .values(total_trades=DailyStat.total_trades + 1)
        )
        await self._session.flush()
        return await self.get_or_create_daily_stat(stat_date=stat_date)

    async def record_daily_loss(self, *, stat_date: date, loss_pct: Decimal) -> DailyStat:
        await self.ensure_daily_stat(stat_date=stat_date)
        await self._session.execute(
            update(DailyStat)
            .where(DailyStat.stat_date == stat_date)
            .values(
                losing_trades=DailyStat.losing_trades + 1,
                consecutive_losses=DailyStat.consecutive_losses + 1,
                daily_loss_pct=func.coalesce(DailyStat.daily_loss_pct, Decimal("0")) + loss_pct,
            )
        )
        await self._session.flush()
        return await self.get_or_create_daily_stat(stat_date=stat_date)

    async def record_daily_win(self, *, stat_date: date) -> DailyStat:
        await self.ensure_daily_stat(stat_date=stat_date)
        await self._session.execute(
            update(DailyStat)
            .where(DailyStat.stat_date == stat_date)
            .values(
                winning_trades=DailyStat.winning_trades + 1,
                consecutive_losses=0,
            )
        )
        await self._session.flush()
        return await self.get_or_create_daily_stat(stat_date=stat_date)

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

    @staticmethod
    def _loss_streak_stat_date(state: SafetyState) -> date:
        if state.last_triggered_at is None:
            return date.today()

        last_triggered_at = state.last_triggered_at
        if last_triggered_at.tzinfo is None:
            last_triggered_at = last_triggered_at.replace(tzinfo=UTC)
        return last_triggered_at.date()

    @staticmethod
    def _update_symbol_stats(symbol: str, stat: DailyStat, *, is_win: bool) -> None:
        symbol_key = symbol.strip().upper()
        existing_stats = dict(stat.symbol_stats or {})
        current_stats = dict(existing_stats.get(symbol_key, {}))
        wins = int(current_stats.get("wins", 0))
        losses = int(current_stats.get("losses", 0))
        consecutive_losses = int(current_stats.get("consecutive_losses", 0))
        if is_win:
            wins += 1
            consecutive_losses = 0
        else:
            losses += 1
            consecutive_losses += 1
        existing_stats[symbol_key] = {
            "wins": wins,
            "losses": losses,
            "consecutive_losses": consecutive_losses,
        }
        stat.symbol_stats = existing_stats

    def _supports_for_update(self) -> bool:
        return self._dialect_name() != "sqlite"

    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name
