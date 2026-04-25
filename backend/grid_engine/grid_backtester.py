from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_state import GridSlot, GridState, SlotStatus

RawCandle = Sequence[object] | Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GridCandle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class GridBacktester:
    def __init__(self, config: GridConfig) -> None:
        self._config = config

    def run(self, candles: Sequence[RawCandle]) -> GridState:
        parsed_candles = [_parse_candle(candle) for candle in candles]
        state = _initialize_state(self._config, parsed_candles)
        for candle in parsed_candles:
            _process_buy_fills(state, candle)
            _process_sell_fills(state, candle)
            _record_snapshot(state, candle)
        return state


def run_grid_backtest(config: GridConfig, candles: Sequence[RawCandle]) -> GridState:
    return GridBacktester(config).run(candles)


def _initialize_state(config: GridConfig, candles: Sequence[GridCandle]) -> GridState:
    state = GridState(config=config)
    starting_price = candles[0].open if candles else config.p_min
    for level, buy_price in enumerate(config.buy_prices()):
        units = _units_for_level(config, buy_price)
        slot = GridSlot(
            level=level,
            buy_price=buy_price,
            sell_price=config.sell_price(buy_price),
            units=units,
        )
        if buy_price >= starting_price:
            slot.status = SlotStatus.WAITING_SELL
            slot.buy_fill_price = buy_price
            state.total_fees_paid += units * buy_price * config.maker_fee_pct
        state.slots.append(slot)
    return state


def _units_for_level(config: GridConfig, buy_price: float) -> float:
    raw_units = config.capital_per_level / buy_price
    if config.min_lot_size == 0:
        return raw_units
    return math.floor(raw_units / config.min_lot_size) * config.min_lot_size


def _process_buy_fills(state: GridState, candle: GridCandle) -> None:
    for slot in state.slots:
        if slot.status != SlotStatus.WAITING_BUY:
            continue
        if candle.low > slot.buy_price:
            continue
        slot.status = SlotStatus.WAITING_SELL
        slot.buy_fill_price = slot.buy_price
        state.total_fees_paid += slot.units * slot.buy_price * state.config.maker_fee_pct


def _process_sell_fills(state: GridState, candle: GridCandle) -> None:
    for slot in state.slots:
        if slot.status != SlotStatus.WAITING_SELL:
            continue
        if candle.high < slot.sell_price:
            continue
        gross_profit = slot.units * (slot.sell_price - slot.buy_price)
        sell_fee = slot.units * slot.sell_price * state.config.maker_fee_pct
        slot.status = SlotStatus.WAITING_BUY
        slot.sell_fill_price = slot.sell_price
        slot.completed_cycles += 1
        slot.realized_pnl_usdt += gross_profit - sell_fee
        state.total_gross_profit += gross_profit
        state.total_fees_paid += sell_fee


def _record_snapshot(state: GridState, candle: GridCandle) -> None:
    inventory_cost = 0.0
    inventory_value = 0.0
    active_buy_orders = 0
    for slot in state.slots:
        if slot.status == SlotStatus.WAITING_SELL:
            inventory_cost += slot.buy_price * slot.units
            inventory_value += candle.close * slot.units
        if slot.status == SlotStatus.WAITING_BUY:
            active_buy_orders += 1

    unrealized_loss = max(0.0, inventory_cost - inventory_value)
    state.unrealized_inventory_usdt = inventory_cost
    state.candle_snapshots.append(
        {
            "open_time_ms": candle.open_time_ms,
            "close": candle.close,
            "unrealized_inventory_usdt": inventory_cost,
            "unrealized_loss_usdt": unrealized_loss,
            "active_buy_orders": active_buy_orders,
        },
    )


def _parse_candle(candle: RawCandle) -> GridCandle:
    if isinstance(candle, Mapping):
        return GridCandle(
            open_time_ms=_mapping_open_time_ms(candle),
            open=_float_from_mapping(candle, "open"),
            high=_float_from_mapping(candle, "high"),
            low=_float_from_mapping(candle, "low"),
            close=_float_from_mapping(candle, "close"),
            volume=_float_from_mapping(candle, "volume"),
        )
    if len(candle) < 6:
        raise ValueError(f"Expected candle with 6 values, got {len(candle)}.")
    return GridCandle(
        open_time_ms=_to_int(candle[0]),
        open=_to_float(candle[1]),
        high=_to_float(candle[2]),
        low=_to_float(candle[3]),
        close=_to_float(candle[4]),
        volume=_to_float(candle[5]),
    )


def _mapping_open_time_ms(candle: Mapping[str, object]) -> int:
    raw_value = candle.get("open_time_ms")
    if raw_value is None:
        raw_value = candle.get("opened_at")
    if raw_value is None:
        return 0
    if isinstance(raw_value, str) and not raw_value.isdigit():
        return 0
    return _to_int(raw_value)


def _float_from_mapping(candle: Mapping[str, object], key: str) -> float:
    raw_value = candle.get(key)
    if raw_value is None:
        raise ValueError(f"Candle missing required key: {key}")
    return _to_float(raw_value)


def _to_float(raw_value: object) -> float:
    if isinstance(raw_value, bool):
        raise TypeError(f"Expected numeric candle value, got {raw_value!r}.")
    if isinstance(raw_value, int | float | str):
        return float(raw_value)
    raise ValueError(f"Expected numeric candle value, got {raw_value!r}.")


def _to_int(raw_value: object) -> int:
    if isinstance(raw_value, bool):
        raise TypeError(f"Expected integer candle value, got {raw_value!r}.")
    if isinstance(raw_value, int | float | str):
        return int(raw_value)
    raise ValueError(f"Expected integer candle value, got {raw_value!r}.")
