from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

class ExecuteSignalRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float
    reason: str | None = None
    strategy_version: str | None = None
    indicators_snapshot: dict[str, object] | None = None


class ExecuteSignalResponse(BaseModel):
    signal_id: UUID
    trade_id: UUID
    order_link_id: str | None
    status: str
    mode: str
    replayed: bool
