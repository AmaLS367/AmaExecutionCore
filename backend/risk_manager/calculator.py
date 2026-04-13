import math

from backend.risk_manager.exceptions import (
    BelowMinNotionalError,
    BelowMinQtyError,
    InvalidRiskInputError,
    ZeroRiskDistanceError,
)


def check_rrr(entry: float, stop: float, target: float, min_rrr: float) -> bool:
    potential_risk = abs(entry - stop)
    potential_profit = abs(target - entry)

    if potential_risk == 0:
        raise ZeroRiskDistanceError("Entry price and Stop price cannot be identical (zero risk distance).")

    ratio = potential_profit / potential_risk
    return ratio >= min_rrr


def calculate_position_raw(equity: float, entry: float, stop: float, risk_pct: float) -> float:
    if equity <= 0:
        raise InvalidRiskInputError(f"Equity must be positive, got {equity}.")
    if risk_pct <= 0:
        raise InvalidRiskInputError(f"Risk percentage must be positive, got {risk_pct}.")
    if entry == stop:
        raise ZeroRiskDistanceError("Entry price and Stop price cannot be identical (zero risk distance).")

    risk_amount = equity * risk_pct
    risk_one_coin = abs(entry - stop)

    return risk_amount / risk_one_coin


def apply_exchange_constraints(qty: float, entry_price: float, qty_step: float, min_qty: float, min_notional: float) -> float:
    if qty <= 0:
        raise InvalidRiskInputError(f"Quantity must be positive, got {qty}.")

    round_qty = math.floor(qty / qty_step) * qty_step
    notional_value = round_qty * entry_price

    if round_qty < min_qty:
        raise BelowMinQtyError(
            f"Calculated quantity {round_qty} is below exchange minimum {min_qty}."
        )

    if notional_value < min_notional:
        raise BelowMinNotionalError(
            f"Notional value {notional_value} is below exchange minimum {min_notional}."
        )

    return round(round_qty, 8)
