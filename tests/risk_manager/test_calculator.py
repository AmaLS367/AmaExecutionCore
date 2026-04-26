import pytest

from backend.risk_manager.calculator import (
    apply_exchange_constraints,
    calculate_position_raw,
    check_rrr,
)
from backend.risk_manager.exceptions import (
    BelowMinNotionalError,
    BelowMinQtyError,
    InvalidRiskInputError,
    ZeroRiskDistanceError,
)

# --- check_rrr ---

def test_check_rrr_long_passes() -> None:
    # risk=10, reward=30 → RRR=3.0 ≥ 2.0
    assert check_rrr(entry=100.0, stop=90.0, target=130.0, min_rrr=2.0) is True


def test_check_rrr_short_passes() -> None:
    # short: entry=100, stop=110 (risk=10), target=70 (reward=30) → RRR=3.0 ≥ 2.0
    assert check_rrr(entry=100.0, stop=110.0, target=70.0, min_rrr=2.0) is True


def test_check_rrr_fails_when_below_min() -> None:
    # risk=10, reward=5 → RRR=0.5 < 2.0
    assert check_rrr(entry=100.0, stop=90.0, target=105.0, min_rrr=2.0) is False


def test_check_rrr_passes_at_exact_boundary() -> None:
    # risk=10, reward=20 → RRR=2.0 == min_rrr → should pass
    assert check_rrr(entry=100.0, stop=90.0, target=120.0, min_rrr=2.0) is True


def test_check_rrr_zero_risk_raises() -> None:
    with pytest.raises(ZeroRiskDistanceError):
        check_rrr(entry=100.0, stop=100.0, target=130.0, min_rrr=2.0)


# --- calculate_position_raw ---

def test_calculate_position_raw_success() -> None:
    # equity=1000, risk=1%, entry=100, stop=90 → risk_amount=10, sl_dist=10 → qty=1.0
    result = calculate_position_raw(equity=1000.0, risk_pct=0.01, entry=100.0, stop=90.0)
    assert result == 1.0


def test_calculate_position_raw_zero_risk_raises() -> None:
    with pytest.raises(ZeroRiskDistanceError):
        calculate_position_raw(equity=1000.0, risk_pct=0.01, entry=100.0, stop=100.0)


def test_calculate_position_raw_zero_equity_raises() -> None:
    with pytest.raises(InvalidRiskInputError):
        calculate_position_raw(equity=0.0, risk_pct=0.01, entry=100.0, stop=90.0)


def test_calculate_position_raw_negative_equity_raises() -> None:
    with pytest.raises(InvalidRiskInputError):
        calculate_position_raw(equity=-500.0, risk_pct=0.01, entry=100.0, stop=90.0)


def test_calculate_position_raw_zero_risk_pct_raises() -> None:
    with pytest.raises(InvalidRiskInputError):
        calculate_position_raw(equity=1000.0, risk_pct=0.0, entry=100.0, stop=90.0)


# --- apply_exchange_constraints ---

def test_apply_exchange_constraints_rounding() -> None:
    result = apply_exchange_constraints(qty=1.2345, qty_step=0.1, entry_price=10.0, min_qty=0.5, min_notional=5.0)
    assert result == 1.2


def test_apply_exchange_constraints_below_min_qty_raises() -> None:
    with pytest.raises(BelowMinQtyError):
        apply_exchange_constraints(qty=0.1, qty_step=0.1, entry_price=10.0, min_qty=0.5, min_notional=0.0)


def test_apply_exchange_constraints_below_notional_raises() -> None:
    with pytest.raises(BelowMinNotionalError):
        apply_exchange_constraints(qty=1.0, qty_step=1.0, entry_price=2.0, min_qty=0.1, min_notional=5.0)


def test_apply_exchange_constraints_zero_qty_raises() -> None:
    with pytest.raises(InvalidRiskInputError):
        apply_exchange_constraints(qty=0.0, qty_step=0.1, entry_price=10.0, min_qty=0.1, min_notional=1.0)


@pytest.mark.parametrize(
    ("qty", "qty_step", "expected"),
    [
        (0.29, 0.01, 0.29),
        (0.30, 0.1, 0.30),
        (0.57, 0.01, 0.57),
        (0.58, 0.01, 0.58),
        (0.70, 0.1, 0.70),
        (1.20, 0.1, 1.20),
        (2.40, 0.1, 2.40),
    ],
)
def test_apply_exchange_constraints_preserves_exact_step_multiples(
    qty: float,
    qty_step: float,
    expected: float,
) -> None:
    result = apply_exchange_constraints(
        qty=qty,
        qty_step=qty_step,
        entry_price=100.0,
        min_qty=qty_step,
        min_notional=0.0,
    )

    assert result == expected


@pytest.mark.parametrize(
    ("qty", "qty_step", "min_qty"),
    [
        (0.30, 0.1, 0.30),
        (1.20, 0.1, 1.20),
        (0.29, 0.01, 0.29),
    ],
)
def test_apply_exchange_constraints_does_not_fall_below_min_qty_at_exact_boundary(
    qty: float,
    qty_step: float,
    min_qty: float,
) -> None:
    result = apply_exchange_constraints(
        qty=qty,
        qty_step=qty_step,
        entry_price=10.0,
        min_qty=min_qty,
        min_notional=0.0,
    )

    assert result == qty


@pytest.mark.parametrize(
    ("qty", "qty_step", "entry_price", "min_notional"),
    [
        (0.30, 0.1, 10.0, 3.0),
        (0.29, 0.01, 100.0, 29.0),
        (1.20, 0.1, 10.0, 12.0),
    ],
)
def test_apply_exchange_constraints_does_not_fall_below_min_notional_at_exact_boundary(
    qty: float,
    qty_step: float,
    entry_price: float,
    min_notional: float,
) -> None:
    result = apply_exchange_constraints(
        qty=qty,
        qty_step=qty_step,
        entry_price=entry_price,
        min_qty=qty_step,
        min_notional=min_notional,
    )

    assert result == qty
