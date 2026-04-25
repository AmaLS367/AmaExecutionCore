from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.grid_engine.grid_config import GridConfig


class SlotStatus(Enum):
    WAITING_BUY = "waiting_buy"
    HOLDING = "holding"
    WAITING_SELL = "waiting_sell"


@dataclass
class GridSlot:
    level: int
    buy_price: float
    sell_price: float
    units: float
    status: SlotStatus = SlotStatus.WAITING_BUY
    buy_fill_price: float = 0.0
    sell_fill_price: float = 0.0
    completed_cycles: int = 0
    realized_pnl_usdt: float = 0.0


@dataclass
class GridState:
    config: GridConfig
    slots: list[GridSlot] = field(default_factory=list)
    total_fees_paid: float = 0.0
    total_gross_profit: float = 0.0
    unrealized_inventory_usdt: float = 0.0
    candle_snapshots: list[dict[str, object]] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return self.total_gross_profit - self.total_fees_paid

    @property
    def completed_cycles(self) -> int:
        return sum(slot.completed_cycles for slot in self.slots)
