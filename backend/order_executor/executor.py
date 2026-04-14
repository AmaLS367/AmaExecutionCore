import asyncio
import uuid
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.bybit_client.exceptions import BybitAPIError, BybitConnectionError
from backend.config import settings
from backend.order_executor.idempotency import generate_order_link_id, is_order_already_submitted
from backend.risk_manager.calculator import (
    apply_exchange_constraints,
    calculate_position_raw,
    check_rrr,
)
from backend.risk_manager.exceptions import RiskManagerError
from backend.safety_guard.circuit_breaker import circuit_breaker
from backend.safety_guard.kill_switch import kill_switch
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)
from backend.trade_journal.store import TradeJournalStore


class OrderAlreadySubmittedError(Exception):
    """Raised when a non-terminal trade for this signal already exists."""


class OrderExecutor:
    """
    Orchestrates the full pre-submission pipeline:
    equity fetch → risk math → RRR check → position limits →
    idempotency guard → safety checks → REST submission (or shadow log).
    """

    def __init__(self, rest_client: Any) -> None:
        self._client = rest_client

    async def execute(
        self,
        session: AsyncSession,
        signal_id: uuid.UUID,
        symbol: str,
        direction: SignalDirection,
        entry: float,
        stop: float,
        target: float,
        category: str = "spot",
    ) -> Trade:
        # 1. Guard: kill switch active?
        await kill_switch.guard(session)

        # 2. Guard: daily loss / consecutive loss limits
        await circuit_breaker.check(session)

        # 4. Guard: trade for this signal already in progress?
        if await is_order_already_submitted(session, signal_id):
            raise OrderAlreadySubmittedError(
                f"Non-terminal trade already exists for signal {signal_id}."
            )

        # 3. Fetch equity (simulated in shadow mode)
        equity: float
        if settings.trading_mode == "shadow":
            equity = settings.shadow_equity
        else:
            balance = await asyncio.to_thread(self._client.get_wallet_balance)
            coin_list = balance.get("list", [{}])[0].get("coin", [])
            usdt = next((c for c in coin_list if c.get("coin") == "USDT"), None)
            equity = float(usdt["equity"]) if usdt else 0.0

        # 4. Position sizing
        qty_raw = calculate_position_raw(
            equity=equity,
            entry=entry,
            stop=stop,
            risk_pct=settings.risk_per_trade_pct,
        )

        # 5. RRR validation
        if not check_rrr(entry=entry, stop=stop, target=target, min_rrr=settings.min_rrr):
            raise RiskManagerError(
                f"RRR below minimum {settings.min_rrr} for signal {signal_id}."
            )

        # 6. Apply exchange constraints (skipped in shadow — no live instrument data needed)
        qty: float
        if settings.trading_mode != "shadow":
            instrument = await asyncio.to_thread(
                self._client.get_instruments_info, symbol, category
            )
            lot = instrument["lotSizeFilter"]
            # Spot uses basePrecision; futures/linear use qtyStep
            qty_step = float(lot.get("qtyStep") or lot.get("basePrecision", "0.000001"))
            qty = apply_exchange_constraints(
                qty=qty_raw,
                entry_price=entry,
                qty_step=qty_step,
                min_qty=float(lot["minOrderQty"]),
                min_notional=float(lot.get("minOrderAmt", 0)),
            )
        else:
            qty = round(qty_raw, 8)

        # 7. Max open positions check
        count_result = await session.execute(
            select(func.count(Trade.id)).where(Trade.status == TradeStatus.POSITION_OPEN)
        )
        open_count: int = count_result.scalar() or 0
        if open_count >= settings.max_open_positions:
            raise RiskManagerError(
                f"Max open positions ({settings.max_open_positions}) already reached."
            )

        await self._ensure_total_exposure_within_limit(session, equity=Decimal(str(equity)))

        # 8. Persist Trade record with RISK_CALCULATED status
        store = TradeJournalStore(session)
        exchange_side = ExchangeSide.BUY if direction == SignalDirection.LONG else ExchangeSide.SELL
        order_link_id = generate_order_link_id(str(signal_id))
        rrr = Decimal(str(abs(target - entry) / abs(entry - stop)))

        trade = Trade(
            signal_id=signal_id,
            order_link_id=order_link_id,
            symbol=symbol,
            signal_direction=direction,
            exchange_side=exchange_side,
            market_type=MarketType.SPOT,
            mode=TradingMode(settings.trading_mode),
            equity_at_entry=Decimal(str(equity)),
            risk_amount_usd=Decimal(str(equity * settings.risk_per_trade_pct)),
            risk_pct=Decimal(str(settings.risk_per_trade_pct)),
            entry_price=Decimal(str(entry)),
            stop_price=Decimal(str(stop)),
            target_price=Decimal(str(target)),
            expected_rrr=rrr,
            qty=Decimal(str(qty)),
            status=TradeStatus.RISK_CALCULATED,
        )
        session.add(trade)
        await session.flush()
        await store.record_trade_created(
            trade,
            event_metadata={"source": "order_executor"},
        )
        await circuit_breaker.increment_trade_count(session)

        # 9. Shadow: log and exit without REST call
        if settings.trading_mode == "shadow":
            await store.transition_trade_status(
                trade,
                TradeStatus.ORDER_SUBMITTED,
                event_metadata={"source": "order_executor", "execution_mode": "shadow"},
            )
            await session.commit()
            logger.info(
                "Shadow order logged. symbol={} side={} qty={} order_link_id={}",
                symbol,
                exchange_side.value,
                qty,
                order_link_id,
            )
            return trade

        # 10. Real/Demo: submit to exchange
        await store.transition_trade_status(
            trade,
            TradeStatus.SAFETY_CHECKED,
            event_metadata={"source": "order_executor"},
        )
        trade.order_type = "Limit"
        try:
            submitted_order_link_id = await self._submit_order(
                session=session,
                trade=trade,
                category=category,
                symbol=symbol,
                exchange_side=exchange_side,
                qty=qty,
                entry=entry,
                stop=stop,
                target=target,
            )
            logger.info(
                "Order submitted. symbol={} order_link_id={} exchange_order_id={}",
                symbol,
                submitted_order_link_id,
                trade.exchange_order_id,
            )
        except BybitAPIError as exc:
            await store.transition_trade_status(
                trade,
                TradeStatus.ORDER_REJECTED,
                event_metadata={"source": "order_executor", "reason": "exchange_rejected"},
            )
            logger.error("Order submission rejected for {}: {}", order_link_id, exc)
            await session.commit()
            raise
        except (BybitConnectionError, TimeoutError) as exc:
            await self._handle_submit_uncertainty(
                session=session,
                trade=trade,
                category=category,
                symbol=symbol,
                exc=exc,
            )

        await session.commit()
        return trade

    async def _submit_order(
        self,
        *,
        session: AsyncSession,
        trade: Trade,
        category: str,
        symbol: str,
        exchange_side: ExchangeSide,
        qty: float,
        entry: float,
        stop: float,
        target: float,
    ) -> str:
        order_mode = settings.order_mode
        current_order_link_id = trade.order_link_id or generate_order_link_id(str(trade.signal_id))

        if order_mode == "taker_allowed":
            # Spot market orders: SL/TP prices are derived from the signal's limit entry price
            # and may be invalid relative to the unknown market fill price — skip them.
            is_spot_market = category == "spot"
            result = await asyncio.to_thread(
                self._client.place_order,
                category=category,
                symbol=symbol,
                side=exchange_side.value,
                order_type="Market",
                qty=str(qty),
                order_link_id=current_order_link_id,
                sl_price=None if is_spot_market else str(stop),
                tp_price=None if is_spot_market else str(target),
                market_unit="baseCoin" if exchange_side == ExchangeSide.BUY else None,
            )
            trade.order_type = "Market"
            trade.is_post_only = False
        else:
            try:
                result = await asyncio.to_thread(
                    self._client.place_order,
                    category=category,
                    symbol=symbol,
                    side=exchange_side.value,
                    order_type="Limit",
                    qty=str(qty),
                    price=str(entry),
                    order_link_id=current_order_link_id,
                    is_post_only=True,
                    sl_price=str(stop),
                    tp_price=str(target),
                )
                trade.order_type = "Limit"
                trade.is_post_only = True
            except BybitAPIError as exc:
                if order_mode == "maker_preferred" and self._looks_like_post_only_rejection(exc):
                    current_order_link_id = generate_order_link_id(str(trade.signal_id))
                    trade.order_link_id = current_order_link_id
                    result = await asyncio.to_thread(
                        self._client.place_order,
                        category=category,
                        symbol=symbol,
                        side=exchange_side.value,
                        order_type="Market",
                        qty=str(qty),
                        order_link_id=current_order_link_id,
                        sl_price=None if category == "spot" else str(stop),
                        tp_price=None if category == "spot" else str(target),
                        market_unit="baseCoin" if exchange_side == ExchangeSide.BUY else None,
                    )
                    trade.order_type = "Market"
                    trade.is_post_only = False
                else:
                    raise

        trade.exchange_order_id = result.get("orderId")
        store = TradeJournalStore(session)
        await store.transition_trade_status(
            trade,
            TradeStatus.ORDER_SUBMITTED,
            event_metadata={"source": "order_executor"},
        )
        trade.order_link_id = current_order_link_id
        return current_order_link_id

    async def _handle_submit_uncertainty(
        self,
        *,
        session: AsyncSession,
        trade: Trade,
        category: str,
        symbol: str,
        exc: Exception,
    ) -> None:
        logger.warning("Order submission uncertain for {}: {}", trade.order_link_id, exc)
        store = TradeJournalStore(session)
        await store.transition_trade_status(
            trade,
            TradeStatus.ORDER_PENDING_UNKNOWN,
            event_metadata={"source": "order_executor", "reason": "submit_uncertain"},
        )
        await self.reconcile_pending_unknown(session=session, trade=trade, category=category, symbol=symbol)
        await session.commit()

    async def reconcile_pending_unknown(
        self,
        *,
        session: AsyncSession,
        trade: Trade,
        category: str = "spot",
        symbol: str | None = None,
    ) -> Trade:
        if trade.order_link_id is None:
            await session.flush()
            return trade

        resolved_symbol = symbol or trade.symbol
        try:
            resolved_order = await asyncio.to_thread(
                self._client.get_order_status,
                category=category,
                symbol=resolved_symbol,
                order_link_id=trade.order_link_id,
            )
        except (BybitConnectionError, TimeoutError) as exc:
            logger.warning(
                "Pending unknown reconciliation still unresolved for {}: {}",
                trade.order_link_id,
                exc,
            )
            await session.flush()
            return trade

        store = TradeJournalStore(session)
        if resolved_order is not None:
            trade.exchange_order_id = resolved_order.get("orderId")
            await store.transition_trade_status(
                trade,
                self._map_remote_order_status(resolved_order.get("orderStatus", "")),
                event_metadata={"source": "pending_unknown_reconciliation"},
            )
        await session.flush()
        return trade

    async def _ensure_total_exposure_within_limit(
        self,
        session: AsyncSession,
        *,
        equity: Decimal,
    ) -> None:
        result = await session.execute(
            select(Trade).where(
                Trade.status.in_(
                    [
                        TradeStatus.POSITION_OPEN,
                        TradeStatus.POSITION_CLOSE_PENDING,
                        TradeStatus.ORDER_PARTIALLY_FILLED,
                    ]
                )
            )
        )
        open_risk = Decimal("0")
        for trade in result.scalars().all():
            risk_amount = trade.risk_amount_usd or Decimal("0")
            if trade.status == TradeStatus.ORDER_PARTIALLY_FILLED and trade.qty and trade.filled_qty:
                risk_amount = risk_amount * (trade.filled_qty / trade.qty)
            open_risk += risk_amount

        max_exposure = equity * Decimal(str(settings.max_total_risk_exposure_pct))
        if open_risk + (equity * Decimal(str(settings.risk_per_trade_pct))) > max_exposure:
            raise RiskManagerError("Total risk exposure limit would be exceeded.")

    @staticmethod
    def _looks_like_post_only_rejection(exc: BybitAPIError) -> bool:
        message = exc.ret_msg.lower()
        return "postonly" in message or "post only" in message

    @staticmethod
    def _map_remote_order_status(order_status: str) -> TradeStatus:
        if order_status == "Filled":
            return TradeStatus.ORDER_CONFIRMED
        if order_status == "Cancelled":
            return TradeStatus.ORDER_CANCELLED
        if order_status == "PartiallyFilled":
            return TradeStatus.ORDER_PARTIALLY_FILLED
        if order_status == "Rejected":
            return TradeStatus.ORDER_REJECTED
        return TradeStatus.ORDER_PENDING_UNKNOWN
