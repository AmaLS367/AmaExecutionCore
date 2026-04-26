from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal


def calculate_max_drawdown(realized_pnls: Iterable[Decimal]) -> Decimal:
    equity_curve = Decimal(0)
    peak_equity = Decimal(0)
    max_drawdown = Decimal(0)
    for realized_pnl in realized_pnls:
        equity_curve += realized_pnl
        peak_equity = max(peak_equity, equity_curve)
        max_drawdown = max(max_drawdown, peak_equity - equity_curve)
    return max_drawdown
