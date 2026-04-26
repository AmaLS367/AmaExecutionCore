from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_ws_handler import GridOrderFillEvent, GridWebSocketHandler
from backend.grid_engine.models import (
    GridSession,
    GridSessionStatus,
    GridSlotRecordStatus,
)
from backend.grid_engine.order_manager import GridOrderManager


class GridPriceClient(Protocol):
    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        ...


@dataclass(frozen=True, slots=True)
class GridDailyReport:
    session_id: int
    completed_cycles_24h: int
    net_pnl_24h: float
    unrealized_position_value: float
    fee_coverage_ratio: float


class GridRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        order_manager: GridOrderManager,
        rest_client: GridPriceClient,
    ) -> None:
        self._session_factory = session_factory
        self._order_manager = order_manager
        self._rest_client = rest_client
        self._ws_handler = GridWebSocketHandler(
            session_factory=session_factory,
            order_manager=order_manager,
        )
        self._last_daily_report_at: dict[int, datetime] = {}

    async def start(self, session_id: int) -> None:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            if grid_session.status == GridSessionStatus.ACTIVE.value:
                raise ValueError(f"Grid session {session_id} is already active")

            current_price = self._get_current_price(grid_session.symbol)

            config = _config_from_json(grid_session.config_json)
            for slot in grid_session.slots:
                units = Decimal(str(config.capital_per_level / float(slot.buy_price)))
                slot.units = units
                if float(slot.buy_price) >= current_price:
                    slot.status = GridSlotRecordStatus.WAITING_SELL.value
                    continue
                if slot.status == GridSlotRecordStatus.WAITING_BUY.value and not slot.buy_order_id:
                    order_id = self._order_manager.place_buy_limit(
                        grid_session.symbol,
                        price=float(slot.buy_price),
                        qty=float(units),
                    )
                    slot.buy_order_id = order_id
            grid_session.status = GridSessionStatus.ACTIVE.value
            await session.commit()

    async def stop(self, session_id: int) -> None:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            self._order_manager.cancel_all_orders(grid_session.symbol)
            grid_session.status = GridSessionStatus.STOPPED.value
            await session.commit()

    async def pause(self, session_id: int) -> None:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            grid_session.status = GridSessionStatus.PAUSED.value
            await session.commit()

    async def handle_order_fill(self, event: GridOrderFillEvent) -> None:
        await self._ws_handler.handle_order_fill(event)

    async def evaluate_risk(
        self,
        session_id: int,
        *,
        current_price: float,
        config_max_dd_pct: float = 35.0,
    ) -> None:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            config = _config_from_json(grid_session.config_json)
            drawdown_pct = _unrealized_drawdown_pct(grid_session, current_price)
            if drawdown_pct > config_max_dd_pct:
                logger.warning(
                    "Grid unrealized drawdown exceeded limit. session_id={} dd_pct={:.2f}",
                    session_id,
                    drawdown_pct,
                )
                await self.pause(session_id)
                return
            if current_price < config.p_min * 0.95:
                logger.warning(
                    "Grid price below lower bound stop. session_id={} price={}",
                    session_id,
                    current_price,
                )
                await self.stop(session_id)

    async def handle_upside_breakout(self, session_id: int) -> bool:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            if not grid_session.slots:
                return False
            if any(slot.status != GridSlotRecordStatus.WAITING_BUY.value for slot in grid_session.slots):
                return False
            total_profit = sum(float(slot.realized_pnl) for slot in grid_session.slots)
            grid_session.status = GridSessionStatus.WAITING_REENTRY.value
            await session.commit()
        logger.info(
            "Grid exhausted upside. All capital in USDT. Consider re-creating grid. "
            "session_id={} total_profit={:.8f}",
            session_id,
            total_profit,
        )
        return True

    async def build_daily_report(self, session_id: int, *, current_price: float) -> GridDailyReport:
        async with self._session_factory() as session:
            grid_session = await self._load_session(session, session_id)
            completed_cycles = sum(slot.completed_cycles for slot in grid_session.slots)
            net_pnl = sum(float(slot.realized_pnl) for slot in grid_session.slots)
            unrealized_value = _unrealized_position_value(grid_session, current_price)
        self._last_daily_report_at[session_id] = datetime.now(UTC)
        logger.info(
            "Grid daily report. session_id={} cycles={} net_pnl={} unrealized_value={}",
            session_id,
            completed_cycles,
            net_pnl,
            unrealized_value,
        )
        return GridDailyReport(
            session_id=session_id,
            completed_cycles_24h=completed_cycles,
            net_pnl_24h=net_pnl,
            unrealized_position_value=unrealized_value,
            fee_coverage_ratio=0.0,
        )

    async def _symbol_for_session(self, session_id: int) -> str:
        async with self._session_factory() as session:
            grid_session = await session.get(GridSession, session_id)
            if grid_session is None:
                raise ValueError(f"Grid session {session_id} was not found.")
            return grid_session.symbol

    async def _load_session(self, session: AsyncSession, session_id: int) -> GridSession:
        statement = (
            select(GridSession)
            .options(selectinload(GridSession.slots))
            .where(GridSession.id == session_id)
        )
        grid_session = (await session.execute(statement)).scalar_one_or_none()
        if grid_session is None:
            raise ValueError(f"Grid session {session_id} was not found.")
        return grid_session

    def _get_current_price(self, symbol: str) -> float:
        return float(self._rest_client.get_ticker_price(symbol, category="spot"))


def _config_from_json(config_json: dict[str, object]) -> GridConfig:
    return GridConfig(
        symbol=str(config_json["symbol"]),
        p_min=_to_float(config_json["p_min"]),
        p_max=_to_float(config_json["p_max"]),
        n_levels=_to_int(config_json["n_levels"]),
        capital_usdt=_to_float(config_json["capital_usdt"]),
        maker_fee_pct=_to_float(config_json.get("maker_fee_pct", 0.001)),
        min_lot_size=_to_float(config_json.get("min_lot_size", 0.0)),
    )


def _to_float(value: object) -> float:
    if isinstance(value, bool):
        raise TypeError(f"Expected numeric config value, got {value!r}.")
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"Expected numeric config value, got {value!r}.")


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError(f"Expected integer config value, got {value!r}.")
    if isinstance(value, int | str):
        return int(value)
    raise TypeError(f"Expected integer config value, got {value!r}.")


def _unrealized_drawdown_pct(grid_session: GridSession, current_price: float) -> float:
    cost = 0.0
    market_value = 0.0
    for slot in grid_session.slots:
        if slot.status != GridSlotRecordStatus.WAITING_SELL.value:
            continue
        units = float(slot.units or Decimal(0))
        cost += float(slot.buy_price) * units
        market_value += current_price * units
    capital = _to_float(grid_session.config_json["capital_usdt"])
    if capital == 0:
        return 0.0
    return max(0.0, cost - market_value) / capital * 100


def _unrealized_position_value(grid_session: GridSession, current_price: float) -> float:
    value = 0.0
    for slot in grid_session.slots:
        if slot.status == GridSlotRecordStatus.WAITING_SELL.value:
            value += current_price * float(slot.units or Decimal(0))
    return value
