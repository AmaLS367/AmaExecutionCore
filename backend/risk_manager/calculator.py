import math

def check_rrr(entry: float, stop: float, target: float, min_rrr: float) -> bool:
    potential_risk = abs(entry - stop)
    potential_profit = abs(target - entry)
    
    if potential_risk == 0:
        raise ValueError("Entry price and Stop price cannot be identical (zero risk).")
        
    ratio = potential_profit / potential_risk 
    return ratio >= min_rrr


def calculate_position_raw(equity: float, entry: float, stop: float, risk_pct: float) -> float:
    if entry == stop:
        raise ValueError("Entry price and Stop price cannot be identical (zero risk).")
        
    risk_amount = equity * risk_pct
    risk_one_coin = abs(entry - stop)
    
    return risk_amount / risk_one_coin


def apply_exchange_constraints(qty: float, entry_price: float, qty_step: float, min_qty: float, min_notional: float) -> float:
    round_qty = math.floor(qty / qty_step) * qty_step
    notional_value = round_qty * entry_price
    
    if round_qty < min_qty:
        raise ValueError(f"Calculated quantity {round_qty} is strictly below exchange minimum {min_qty}")
        
    if notional_value < min_notional:
        raise ValueError(f"Notional value {notional_value} is below exchange minimum notional {min_notional}")
        
    return round(round_qty, 8)
