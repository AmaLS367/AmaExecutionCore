from __future__ import annotations

from decimal import Decimal

from backend.backtest.metrics import calculate_max_drawdown


def test_calculate_max_drawdown_tracks_peak_to_trough_loss() -> None:
    assert calculate_max_drawdown(
        (
            Decimal(10),
            Decimal(-5),
            Decimal(-15),
            Decimal(20),
        ),
    ) == Decimal(20)
