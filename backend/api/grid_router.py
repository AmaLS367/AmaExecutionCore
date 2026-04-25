from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.grid_engine.grid_advisor import suggest_grid
from backend.grid_engine.grid_backtester import RawCandle
from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_runner import GridRunner
from backend.grid_engine.models import (
    GridSession,
    GridSessionStatus,
    GridSlotRecord,
    GridSlotRecordStatus,
)

router = APIRouter(prefix="/grid", tags=["grid"])

REGRESSION_DIR = Path("scripts/fixtures/regression")
DEFAULT_LOOKBACK_DAYS = 30
INTERVALS_PER_DAY = 24 * 4


class GridSuggestRequest(BaseModel):
    symbol: str
    capital_usdt: float = Field(gt=0)
    lookback_days: int = Field(default=DEFAULT_LOOKBACK_DAYS, ge=1)


class GridSuggestResponse(BaseModel):
    p_min: float
    p_max: float
    n_levels: int
    step_pct: float
    estimated_annual_yield_pct: float


class GridCreateRequest(BaseModel):
    symbol: str
    p_min: float = Field(gt=0)
    p_max: float = Field(gt=0)
    n_levels: int = Field(ge=1)
    capital_usdt: float = Field(gt=0)


class GridSlotResponse(BaseModel):
    id: int | None = None
    level: int
    buy_price: float
    sell_price: float
    status: str
    completed_cycles: int
    realized_pnl: float


class GridCreateResponse(BaseModel):
    session_id: int
    slots: list[GridSlotResponse]
    step_pct: float
    warning_if_step_too_small: str | None


class GridStatusResponse(BaseModel):
    session_id: int
    symbol: str
    status: str
    completed_cycles: int
    net_pnl_usdt: float
    fee_coverage_ratio: float
    max_unrealized_drawdown_pct: float
    slots: list[GridSlotResponse]


class GridStateResponse(BaseModel):
    session_id: int
    status: str


@router.post("/suggest", response_model=GridSuggestResponse)
async def suggest_grid_endpoint(payload: GridSuggestRequest) -> GridSuggestResponse:
    candles = _load_recent_fixture_candles(payload.symbol, payload.lookback_days)
    config = suggest_grid(
        candles,
        capital_usdt=payload.capital_usdt,
        symbol=payload.symbol.strip().upper(),
    )
    return GridSuggestResponse(
        p_min=config.p_min,
        p_max=config.p_max,
        n_levels=config.n_levels,
        step_pct=config.step_pct,
        estimated_annual_yield_pct=0.0,
    )


@router.post("/create", response_model=GridCreateResponse)
async def create_grid_session(request: Request, payload: GridCreateRequest) -> GridCreateResponse:
    config = GridConfig(
        symbol=payload.symbol.strip().upper(),
        p_min=payload.p_min,
        p_max=payload.p_max,
        n_levels=payload.n_levels,
        capital_usdt=payload.capital_usdt,
    )
    session_record = GridSession(
        symbol=config.symbol,
        config_json=_config_to_json(config),
        status=GridSessionStatus.PAUSED.value,
    )
    session_record.slots = [
        GridSlotRecord(
            level=level,
            buy_price=Decimal(str(buy_price)),
            sell_price=Decimal(str(config.sell_price(buy_price))),
            status=GridSlotRecordStatus.WAITING_BUY.value,
            completed_cycles=0,
            realized_pnl=Decimal(0),
        )
        for level, buy_price in enumerate(config.buy_prices())
    ]

    session_factory = _session_factory(request)
    async with session_factory() as session:
        session.add(session_record)
        await session.commit()
        await session.refresh(session_record)
        statement = (
            select(GridSession)
            .options(selectinload(GridSession.slots))
            .where(GridSession.id == session_record.id)
        )
        persisted = (await session.execute(statement)).scalar_one()

    return GridCreateResponse(
        session_id=persisted.id,
        slots=[_slot_response(slot) for slot in persisted.slots],
        step_pct=config.step_pct,
        warning_if_step_too_small=(
            "Grid step is below 0.5%; fees may consume most cycle profit."
            if config.step_pct < 0.005
            else None
        ),
    )


@router.get("/{session_id}/status", response_model=GridStatusResponse)
async def get_grid_status(request: Request, session_id: int) -> GridStatusResponse:
    session_record = await _load_grid_session(request, session_id)
    completed_cycles = sum(slot.completed_cycles for slot in session_record.slots)
    net_pnl = sum(float(slot.realized_pnl) for slot in session_record.slots)
    return GridStatusResponse(
        session_id=session_record.id,
        symbol=session_record.symbol,
        status=session_record.status,
        completed_cycles=completed_cycles,
        net_pnl_usdt=net_pnl,
        fee_coverage_ratio=0.0,
        max_unrealized_drawdown_pct=0.0,
        slots=[_slot_response(slot) for slot in session_record.slots],
    )


@router.post("/{session_id}/start", response_model=GridStateResponse)
async def start_grid_session(request: Request, session_id: int) -> GridStateResponse:
    runner = _grid_runner(request)
    await runner.start(session_id)
    return GridStateResponse(session_id=session_id, status=GridSessionStatus.ACTIVE.value)


@router.post("/{session_id}/pause", response_model=GridStateResponse)
async def pause_grid_session(request: Request, session_id: int) -> GridStateResponse:
    return await _set_session_status(request, session_id, GridSessionStatus.PAUSED)


@router.post("/{session_id}/stop", response_model=GridStateResponse)
async def stop_grid_session(request: Request, session_id: int) -> GridStateResponse:
    runner = _grid_runner(request)
    await runner.stop(session_id)
    return GridStateResponse(session_id=session_id, status=GridSessionStatus.STOPPED.value)


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)


def _grid_runner(request: Request) -> GridRunner:
    return cast("GridRunner", request.app.state.grid_runner)


async def _load_grid_session(request: Request, session_id: int) -> GridSession:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        statement = (
            select(GridSession)
            .options(selectinload(GridSession.slots))
            .where(GridSession.id == session_id)
        )
        session_record = (await session.execute(statement)).scalar_one_or_none()
    if session_record is None:
        raise HTTPException(status_code=404, detail=f"Grid session {session_id} was not found.")
    return session_record


async def _set_session_status(
    request: Request,
    session_id: int,
    status: GridSessionStatus,
) -> GridStateResponse:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        session_record = await session.get(GridSession, session_id)
        if session_record is None:
            raise HTTPException(status_code=404, detail=f"Grid session {session_id} was not found.")
        session_record.status = status.value
        await session.commit()
    return GridStateResponse(session_id=session_id, status=status.value)


def _config_to_json(config: GridConfig) -> dict[str, object]:
    return {
        "symbol": config.symbol,
        "p_min": config.p_min,
        "p_max": config.p_max,
        "n_levels": config.n_levels,
        "capital_usdt": config.capital_usdt,
        "maker_fee_pct": config.maker_fee_pct,
        "min_lot_size": config.min_lot_size,
    }


def _slot_response(slot: GridSlotRecord) -> GridSlotResponse:
    return GridSlotResponse(
        id=slot.id,
        level=slot.level,
        buy_price=float(slot.buy_price),
        sell_price=float(slot.sell_price),
        status=slot.status,
        completed_cycles=slot.completed_cycles,
        realized_pnl=float(slot.realized_pnl),
    )


def _load_recent_fixture_candles(symbol: str, lookback_days: int) -> list[RawCandle]:
    normalized_symbol = symbol.strip().lower()
    path = REGRESSION_DIR / f"{normalized_symbol}_15m_365d.json.gz"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No fixture data available for {symbol}.")
    candles = _load_fixture(path)
    lookback_count = lookback_days * INTERVALS_PER_DAY
    return candles[-lookback_count:]


def _load_fixture(path: Path) -> list[RawCandle]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        loaded: object = json.load(file)
    candles = loaded.get("candles") if isinstance(loaded, Mapping) else loaded
    if not isinstance(candles, list):
        raise TypeError(f"Fixture {path} must contain a candle list.")
    return [_normalize_candle(candle) for candle in candles]


def _normalize_candle(candle: object) -> RawCandle:
    if isinstance(candle, Mapping):
        normalized: dict[str, object] = {}
        for key, value in candle.items():
            if not isinstance(key, str):
                raise TypeError(f"Candle key must be a string, got {key!r}.")
            normalized[key] = value
        return normalized
    if isinstance(candle, list):
        return list(candle)
    raise TypeError(f"Unsupported candle format: {candle!r}")
