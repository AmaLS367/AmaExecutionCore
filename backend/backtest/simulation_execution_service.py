from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest


@dataclass(slots=True, frozen=True)
class SimulationExecutionResult:
    realized_pnl: Decimal
    fees_paid: Decimal
    slippage: Decimal
    exit_reason: str
    hold_candles: int


class SimulationExecutionService:
    def __init__(
        self,
        *,
        max_hold_candles: int = 20,
        risk_amount_usd: float = 100.0,
        fee_rate_per_side: float = 0.0,
    ) -> None:
        self._max_hold_candles = max_hold_candles
        self._risk_amount = Decimal(str(risk_amount_usd))
        self._fee_rate_per_side = Decimal(str(fee_rate_per_side))

    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> SimulationExecutionResult:
        del step_index
        entry = Decimal(str(signal.entry))
        stop = Decimal(str(signal.stop))
        target = Decimal(str(signal.target))
        is_long = signal.direction == "long"
        risk = abs(entry - stop)
        reward = abs(target - entry)
        qty = self._risk_amount / risk if risk else Decimal(0)

        for index, candle in enumerate(future_candles[: self._max_hold_candles]):
            high = Decimal(str(candle.high))
            low = Decimal(str(candle.low))
            if is_long:
                if low <= stop:
                    return SimulationExecutionResult(
                        realized_pnl=-self._risk_amount,
                        fees_paid=self._calculate_fees(entry=entry, exit_price=stop, qty=qty),
                        slippage=Decimal(0),
                        exit_reason="sl_hit",
                        hold_candles=index + 1,
                    )
                if high >= target:
                    rrr = reward / risk if risk else Decimal(0)
                    return SimulationExecutionResult(
                        realized_pnl=self._risk_amount * rrr,
                        fees_paid=self._calculate_fees(entry=entry, exit_price=target, qty=qty),
                        slippage=Decimal(0),
                        exit_reason="tp_hit",
                        hold_candles=index + 1,
                    )
            else:
                if high >= stop:
                    return SimulationExecutionResult(
                        realized_pnl=-self._risk_amount,
                        fees_paid=self._calculate_fees(entry=entry, exit_price=stop, qty=qty),
                        slippage=Decimal(0),
                        exit_reason="sl_hit",
                        hold_candles=index + 1,
                    )
                if low <= target:
                    rrr = reward / risk if risk else Decimal(0)
                    return SimulationExecutionResult(
                        realized_pnl=self._risk_amount * rrr,
                        fees_paid=self._calculate_fees(entry=entry, exit_price=target, qty=qty),
                        slippage=Decimal(0),
                        exit_reason="tp_hit",
                        hold_candles=index + 1,
                    )

        if not future_candles:
            return SimulationExecutionResult(
                realized_pnl=Decimal(0),
                fees_paid=Decimal(0),
                slippage=Decimal(0),
                exit_reason="timeout",
                hold_candles=0,
            )

        last_close = Decimal(str(future_candles[min(self._max_hold_candles, len(future_candles)) - 1].close))
        if is_long:
            pnl = ((last_close - entry) / risk) * self._risk_amount if risk else Decimal(0)
        else:
            pnl = ((entry - last_close) / risk) * self._risk_amount if risk else Decimal(0)
        return SimulationExecutionResult(
            realized_pnl=pnl,
            fees_paid=self._calculate_fees(entry=entry, exit_price=last_close, qty=qty),
            slippage=Decimal(0),
            exit_reason="timeout",
            hold_candles=min(self._max_hold_candles, len(future_candles)),
        )

    def _calculate_fees(self, *, entry: Decimal, exit_price: Decimal, qty: Decimal) -> Decimal:
        if qty <= 0 or self._fee_rate_per_side <= 0:
            return Decimal(0)
        return ((entry * qty) + (exit_price * qty)) * self._fee_rate_per_side
