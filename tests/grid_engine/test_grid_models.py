import pytest

from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_state import GridSlot, GridState, SlotStatus


def test_grid_config_a1_gate_values() -> None:
    config = GridConfig(
        symbol="XRPUSDT",
        p_min=1.80,
        p_max=2.20,
        n_levels=10,
        capital_usdt=20.0,
    )

    assert config.step == pytest.approx(0.04)
    assert config.step_pct == pytest.approx(0.0222, abs=1e-4)
    assert config.step_pct > 0.005
    assert len(config.buy_prices()) == 10
    assert config.sell_price(1.80) == pytest.approx(1.84)
    assert config.capital_per_level == pytest.approx(2.0)


def test_grid_state_a1_imports_and_properties() -> None:
    config = GridConfig(
        symbol="XRPUSDT",
        p_min=1.80,
        p_max=2.20,
        n_levels=10,
        capital_usdt=20.0,
    )
    slot = GridSlot(
        level=0,
        buy_price=1.80,
        sell_price=1.84,
        units=1.0,
        status=SlotStatus.WAITING_SELL,
        completed_cycles=2,
    )
    state = GridState(
        config=config,
        slots=[slot],
        total_gross_profit=1.25,
        total_fees_paid=0.25,
    )

    assert state.completed_cycles == 2
    assert state.net_pnl == pytest.approx(1.0)
