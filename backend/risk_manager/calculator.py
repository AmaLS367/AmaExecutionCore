from decimal import ROUND_DOWN, Decimal

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

    qty_decimal = Decimal(str(qty))
    qty_step_decimal = Decimal(str(qty_step))
    entry_price_decimal = Decimal(str(entry_price))
    min_qty_decimal = Decimal(str(min_qty))
    min_notional_decimal = Decimal(str(min_notional))

    steps = (qty_decimal / qty_step_decimal).to_integral_value(rounding=ROUND_DOWN)
    round_qty_decimal = steps * qty_step_decimal
    notional_value_decimal = round_qty_decimal * entry_price_decimal
    round_qty = float(round_qty_decimal)
    notional_value = float(notional_value_decimal)

    if round_qty_decimal < min_qty_decimal:
        raise BelowMinQtyError(
            f"Calculated quantity {round_qty} is below exchange minimum {min_qty}.",
        )

    if notional_value_decimal < min_notional_decimal:
        raise BelowMinNotionalError(
            f"Notional value {notional_value} is below exchange minimum {min_notional}.",
        )

    return float(round_qty_decimal)
