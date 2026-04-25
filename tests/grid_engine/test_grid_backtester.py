from backend.grid_engine.grid_backtester import run_grid_backtest
from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_metrics import evaluate_grid_backtest


def test_grid_backtester_a2_synthetic_oscillation_gate() -> None:
    candles = [
        [idx * 900_000, 2.0, 2.10, 1.90, 2.0, 1000.0]
        for idx in range(500)
    ]
    config = GridConfig(
        symbol="XRPUSDT",
        p_min=1.80,
        p_max=2.20,
        n_levels=10,
        capital_usdt=20.0,
    )

    state = run_grid_backtest(config, candles)
    result = evaluate_grid_backtest(state, config, backtest_days=6)

    assert result.completed_cycles >= 20
    assert result.net_pnl_usdt > 0
    assert result.fee_coverage_ratio >= 1.5
