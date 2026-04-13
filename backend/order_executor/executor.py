import asyncio
import uuid
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.order_executor.idempotency import generate_order_link_id, is_order_already_submitted
from backend.risk_manager.calculator import (
    apply_exchange_constraints,
    calculate_position_raw,
    check_rrr,
)
from backend.risk_manager.exceptions import RiskManagerError
from backend.safety_guard.kill_switch import kill_switch
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class OrderAlreadySubmittedError(Exception):
    """Raised when a non-terminal trade for this signal already exists."""


class OrderExecutor:
    """
    Orchestrates the full pre-submission pipeline:
    equity fetch → risk math → RRR check → position limits →
    idempotency guard → safety checks → REST submission (or shadow log).
    """

    def __init__(self, rest_client: BybitRESTClient) -> None:
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
        kill_switch.guard()

        # 2. Guard: trade for this signal already in progress?
        if await is_order_already_submitted(session, signal_id):
            raise OrderAlreadySubmittedError(
                f"Non-terminal trade already exists for signal {signal_id}."
            )

        # 3. Fetch equity (simulated in shadow mode)
        equity: float
        if settings.trading_mode == "shadow":
            equity = 10_000.0
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
            qty = apply_exchange_constraints(
                qty=qty_raw,
                entry_price=entry,
                qty_step=float(lot["qtyStep"]),
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

        # 8. Persist Trade record with RISK_CALCULATED status
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

        # 9. Shadow: log and exit without REST call
        if settings.trading_mode == "shadow":
            trade.status = TradeStatus.ORDER_SUBMITTED
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
        trade.status = TradeStatus.SAFETY_CHECKED
        try:
            result = await asyncio.to_thread(
                self._client.place_order,
                category=category,
                symbol=symbol,
                side=exchange_side.value,
                order_type="Limit",
                qty=str(qty),
                price=str(entry),
                order_link_id=order_link_id,
                is_post_only=(settings.order_mode == "maker_only"),
                sl_price=str(stop),
                tp_price=str(target),
            )
            trade.exchange_order_id = result.get("orderId")
            trade.status = TradeStatus.ORDER_SUBMITTED
            logger.info(
                "Order submitted. symbol={} order_link_id={} exchange_order_id={}",
                symbol,
                order_link_id,
                trade.exchange_order_id,
            )
        except Exception as exc:
            trade.status = TradeStatus.ORDER_REJECTED
            logger.error("Order submission failed for {}: {}", order_link_id, exc)
            await session.commit()
            raise

        await session.commit()
        return trade
