import pytest
from backend.risk_manager.calculator import check_rrr, calculate_position_raw, apply_exchange_constraints

def test_check_rrr_long_success() -> None:
    result = check_rrr(entry=100.0, stop=90.0, target=130.0, min_rrr=2.0)
    assert result is True

def test_check_rrr_short_success() -> None:
    result = check_rrr(entry=100.0, stop=110.0, target=115.0, min_rrr=2.0)
    assert result is False

def test_check_rrr_zero_risk() -> None:
    with pytest.raises(ValueError):
        check_rrr(entry=100.0, stop=100.0, target=130.0, min_rrr=2.0)

def test_calculate_position_raw_success() -> None:
    result = calculate_position_raw(equity=1000.0, risk_pct=0.01, entry=100.0, stop=90.0)
    assert result == 1.0

def test_calculate_position_raw_zero_risk() -> None:
    with pytest.raises(ValueError):
        calculate_position_raw(equity=1000.0, risk_pct=0.01, entry=100.0, stop=100.0)

def test_apply_exchange_constraints_rounding() -> None:
    res = apply_exchange_constraints(qty=1.2345, qty_step=0.1, entry_price=10.0, min_qty=0.5, min_notional=5.0)
    assert res == 1.2

def test_apply_exchange_constraints_below_min_qty() -> None:
    with pytest.raises(ValueError):
        apply_exchange_constraints(qty=0.1, qty_step=0.1, entry_price=10.0, min_qty=0.5, min_notional=0.0)

def test_apply_exchange_constraints_below_notional() -> None:
    with pytest.raises(ValueError):
        apply_exchange_constraints(qty=1.0, qty_step=1.0, entry_price=2.0, min_qty=0.1, min_notional=5.0)
