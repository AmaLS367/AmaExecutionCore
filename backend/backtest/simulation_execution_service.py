from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from random import Random

from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest


@dataclass(slots=True, frozen=True)
class SimulationExecutionResult:
    realized_pnl: Decimal
    fees_paid: Decimal
    slippage: Decimal
    exit_reason: str
    hold_candles: int
    closed_at_step: int = 0
    entry_price: Decimal = Decimal(0)
    exit_price: Decimal = Decimal(0)


class SimulationExecutionService:
    def __init__(
        self,
        *,
        max_hold_candles: int = 20,
        risk_amount_usd: float = 100.0,
        fee_rate_per_side: float | None = None,
        maker_fee_rate: Decimal = Decimal("0.001"),
        taker_fee_rate: Decimal = Decimal("0.0025"),
        maker_fill_probability: float = 0.70,
        spread_bps: Decimal = Decimal(2),
        slippage_bps: Decimal = Decimal(1),
        one_bar_execution_delay: bool = True,
    ) -> None:
        self._max_hold_candles = max_hold_candles
        self._risk_amount = Decimal(str(risk_amount_usd))
        self._random = Random(0)  # noqa: S311 - deterministic replay behavior is intentional

        if fee_rate_per_side is not None:
            fee_rate_decimal = Decimal(str(fee_rate_per_side))
            self._maker_fee_rate = fee_rate_decimal
            self._taker_fee_rate = fee_rate_decimal
            self._maker_fill_probability = 1.0
            self._spread_bps = Decimal(0)
            self._slippage_bps = Decimal(0)
            self._one_bar_execution_delay = False
            return

        self._maker_fee_rate = maker_fee_rate
        self._taker_fee_rate = taker_fee_rate
        self._maker_fill_probability = maker_fill_probability
        self._spread_bps = spread_bps
        self._slippage_bps = slippage_bps
        self._one_bar_execution_delay = one_bar_execution_delay

    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> SimulationExecutionResult:
        if not future_candles:
            signal_entry = Decimal(str(signal.entry))
            return SimulationExecutionResult(
                realized_pnl=Decimal(0),
                fees_paid=Decimal(0),
                slippage=Decimal(0),
                exit_reason="timeout",
                hold_candles=0,
                closed_at_step=step_index,
                entry_price=signal_entry,
                exit_price=signal_entry,
            )

        raw_entry_price = self._resolve_entry_price(signal=signal, future_candles=future_candles)
        is_long = signal.direction == "long"
        entry_is_maker = self._maker_fill()
        entry_fee_rate = self._maker_fee_rate if entry_is_maker else self._taker_fee_rate
        entry_price = self._apply_spread(
            price=raw_entry_price,
            direction=signal.direction,
            bps=self._spread_bps / Decimal(2) if not entry_is_maker else Decimal(0),
        )

        stop = Decimal(str(signal.stop))
        target = Decimal(str(signal.target))
        risk = abs(entry_price - stop)
        qty = self._risk_amount / risk if risk else Decimal(0)

        for index, candle in enumerate(future_candles[: self._max_hold_candles]):
            high = Decimal(str(candle.high))
            low = Decimal(str(candle.low))
            hold_candles = index + 1
            closed_at_step = step_index + hold_candles

            if is_long and low <= stop:
                exit_price = self._apply_slippage(price=stop, direction=signal.direction)
                return self._build_result(
                    signal=signal,
                    qty=qty,
                    raw_entry_price=raw_entry_price,
                    entry_price=entry_price,
                    entry_fee_rate=entry_fee_rate,
                    raw_exit_price=stop,
                    exit_price=exit_price,
                    exit_fee_rate=self._taker_fee_rate,
                    exit_reason="sl_hit",
                    hold_candles=hold_candles,
                    closed_at_step=closed_at_step,
                )
            if is_long and high >= target:
                exit_is_maker = self._maker_fill()
                exit_fee_rate = self._maker_fee_rate if exit_is_maker else self._taker_fee_rate
                exit_price = self._apply_spread(
                    price=target,
                    direction=signal.direction,
                    bps=self._spread_bps / Decimal(2) if not exit_is_maker else Decimal(0),
                )
                return self._build_result(
                    signal=signal,
                    qty=qty,
                    raw_entry_price=raw_entry_price,
                    entry_price=entry_price,
                    entry_fee_rate=entry_fee_rate,
                    raw_exit_price=target,
                    exit_price=exit_price,
                    exit_fee_rate=exit_fee_rate,
                    exit_reason="tp_hit",
                    hold_candles=hold_candles,
                    closed_at_step=closed_at_step,
                )
            if (not is_long) and high >= stop:
                exit_price = self._apply_slippage(price=stop, direction=signal.direction)
                return self._build_result(
                    signal=signal,
                    qty=qty,
                    raw_entry_price=raw_entry_price,
                    entry_price=entry_price,
                    entry_fee_rate=entry_fee_rate,
                    raw_exit_price=stop,
                    exit_price=exit_price,
                    exit_fee_rate=self._taker_fee_rate,
                    exit_reason="sl_hit",
                    hold_candles=hold_candles,
                    closed_at_step=closed_at_step,
                )
            if (not is_long) and low <= target:
                exit_is_maker = self._maker_fill()
                exit_fee_rate = self._maker_fee_rate if exit_is_maker else self._taker_fee_rate
                exit_price = self._apply_spread(
                    price=target,
                    direction=signal.direction,
                    bps=self._spread_bps / Decimal(2) if not exit_is_maker else Decimal(0),
                )
                return self._build_result(
                    signal=signal,
                    qty=qty,
                    raw_entry_price=raw_entry_price,
                    entry_price=entry_price,
                    entry_fee_rate=entry_fee_rate,
                    raw_exit_price=target,
                    exit_price=exit_price,
                    exit_fee_rate=exit_fee_rate,
                    exit_reason="tp_hit",
                    hold_candles=hold_candles,
                    closed_at_step=closed_at_step,
                )

        timeout_hold_candles = min(self._max_hold_candles, len(future_candles))
        raw_exit_price = Decimal(str(future_candles[timeout_hold_candles - 1].close))
        exit_price = self._apply_slippage(price=raw_exit_price, direction=signal.direction)
        return self._build_result(
            signal=signal,
            qty=qty,
            raw_entry_price=raw_entry_price,
            entry_price=entry_price,
            entry_fee_rate=entry_fee_rate,
            raw_exit_price=raw_exit_price,
            exit_price=exit_price,
            exit_fee_rate=self._taker_fee_rate,
            exit_reason="timeout",
            hold_candles=timeout_hold_candles,
            closed_at_step=step_index + timeout_hold_candles,
        )

    def _resolve_entry_price(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
    ) -> Decimal:
        if self._one_bar_execution_delay:
            return Decimal(str(future_candles[0].open))
        return Decimal(str(signal.entry))

    def _build_result(
        self,
        *,
        signal: ExecuteSignalRequest,
        qty: Decimal,
        raw_entry_price: Decimal,
        entry_price: Decimal,
        entry_fee_rate: Decimal,
        raw_exit_price: Decimal,
        exit_price: Decimal,
        exit_fee_rate: Decimal,
        exit_reason: str,
        hold_candles: int,
        closed_at_step: int,
    ) -> SimulationExecutionResult:
        fees_paid = self._calculate_fees(
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            entry_fee_rate=entry_fee_rate,
            exit_fee_rate=exit_fee_rate,
        )
        slippage = abs(entry_price - raw_entry_price) + abs(exit_price - raw_exit_price)
        return SimulationExecutionResult(
            realized_pnl=self._calculate_realized_pnl(
                direction=signal.direction,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
            ),
            fees_paid=fees_paid,
            slippage=slippage,
            exit_reason=exit_reason,
            hold_candles=hold_candles,
            closed_at_step=closed_at_step,
            entry_price=entry_price,
            exit_price=exit_price,
        )

    def _calculate_fees(
        self,
        *,
        entry_price: Decimal,
        exit_price: Decimal,
        qty: Decimal,
        entry_fee_rate: Decimal,
        exit_fee_rate: Decimal,
    ) -> Decimal:
        if qty <= 0:
            return Decimal(0)
        entry_notional = entry_price * qty
        exit_notional = exit_price * qty
        return (entry_notional * entry_fee_rate) + (exit_notional * exit_fee_rate)

    def _calculate_realized_pnl(
        self,
        *,
        direction: str,
        entry_price: Decimal,
        exit_price: Decimal,
        qty: Decimal,
    ) -> Decimal:
        if qty <= 0:
            return Decimal(0)
        if direction == "long":
            return (exit_price - entry_price) * qty
        return (entry_price - exit_price) * qty

    def _apply_spread(
        self,
        *,
        price: Decimal,
        direction: str,
        bps: Decimal,
    ) -> Decimal:
        move = (price * bps) / Decimal(10000)
        if direction == "long":
            return price + move
        return price - move

    def _apply_slippage(
        self,
        *,
        price: Decimal,
        direction: str,
    ) -> Decimal:
        move = (price * self._slippage_bps) / Decimal(10000)
        if direction == "long":
            return price - move
        return price + move

    def _maker_fill(self) -> bool:
        if self._maker_fill_probability <= 0.0:
            return False
        if self._maker_fill_probability >= 1.0:
            return True
        return self._random.random() < self._maker_fill_probability
