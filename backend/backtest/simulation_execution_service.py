from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from random import Random
from typing import Literal

from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest

MarketMode = Literal["spot", "derivatives"]
ExecutionSide = Literal["buy", "sell"]

_DEFAULT_SPOT_MIN_NOTIONALS: dict[str, Decimal] = {
    "BTCUSDT": Decimal(5),
    "ETHUSDT": Decimal(5),
    "SOLUSDT": Decimal(5),
    "XRPUSDT": Decimal(5),
}


@dataclass(slots=True, frozen=True)
class SimulationExecutionResult:
    realized_pnl: Decimal
    fees_paid: Decimal
    slippage: Decimal
    exit_reason: str
    hold_candles: int
    qty: Decimal = Decimal(0)
    closed_at_step: int = 0
    entry_price: Decimal = Decimal(0)
    exit_price: Decimal = Decimal(0)
    status: str = "closed"
    rejected_short_signal: bool = False
    skipped_min_notional: bool = False
    skipped_insufficient_capital: bool = False
    ambiguous_candle: bool = False


@dataclass(slots=True, frozen=True)
class _PreparedExecution:
    raw_entry_price: Decimal
    entry_price: Decimal
    entry_fee_rate: Decimal
    stop: Decimal
    target: Decimal
    qty: Decimal


class SimulationExecutionService:
    def __init__(
        self,
        *,
        max_hold_candles: int = 20,
        risk_amount_usd: float = 100.0,
        fee_rate_per_side: float | None = None,
        legacy_fee_shortcut: bool = False,
        maker_fee_rate: Decimal = Decimal("0.001"),
        taker_fee_rate: Decimal = Decimal("0.0025"),
        maker_fill_probability: float = 0.70,
        spread_bps: Decimal = Decimal(2),
        slippage_bps: Decimal = Decimal(1),
        one_bar_execution_delay: bool = True,
        market_mode: MarketMode = "spot",
        virtual_equity_usd: float = 10_000.0,
        min_notional_by_symbol: Mapping[str, Decimal | int | float | str] | None = None,
        default_min_notional: Decimal = Decimal(5),
    ) -> None:
        self._max_hold_candles = max_hold_candles
        self._risk_amount = Decimal(str(risk_amount_usd))
        self._random = Random(0)  # noqa: S311 - deterministic replay behavior is intentional
        if market_mode not in {"spot", "derivatives"}:
            raise ValueError(f"Unsupported market_mode: {market_mode}")
        self._market_mode = market_mode
        self._virtual_equity = Decimal(str(virtual_equity_usd))
        self._default_min_notional = default_min_notional
        self._min_notional_by_symbol = {
            **_DEFAULT_SPOT_MIN_NOTIONALS,
            **{
                symbol.strip().upper(): Decimal(str(value))
                for symbol, value in (min_notional_by_symbol or {}).items()
            },
        }

        fee_rate_decimal = Decimal(str(fee_rate_per_side)) if fee_rate_per_side is not None else None
        if fee_rate_decimal is not None and legacy_fee_shortcut:
            self._maker_fee_rate = fee_rate_decimal
            self._taker_fee_rate = fee_rate_decimal
            self._maker_fill_probability = 1.0
            self._spread_bps = Decimal(0)
            self._slippage_bps = Decimal(0)
            self._one_bar_execution_delay = False
            return

        self._maker_fee_rate = fee_rate_decimal if fee_rate_decimal is not None else maker_fee_rate
        self._taker_fee_rate = fee_rate_decimal if fee_rate_decimal is not None else taker_fee_rate
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
        signal_entry = Decimal(str(signal.entry))
        skipped_result = self._maybe_skip_spot_short(
            signal=signal,
            signal_entry=signal_entry,
            step_index=step_index,
        )
        if skipped_result is not None:
            return skipped_result
        if not future_candles:
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
        prepared_execution = self._prepare_execution(
            signal=signal,
            future_candles=future_candles,
            step_index=step_index,
        )
        if isinstance(prepared_execution, SimulationExecutionResult):
            return prepared_execution

        for index, candle in enumerate(future_candles[: self._max_hold_candles]):
            exit_result = self._evaluate_candle_exit(
                signal=signal,
                candle=candle,
                index=index,
                step_index=step_index,
                prepared_execution=prepared_execution,
            )
            if exit_result is not None:
                return exit_result

        return self._build_timeout_result(
            signal=signal,
            future_candles=future_candles,
            step_index=step_index,
            prepared_execution=prepared_execution,
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
        ambiguous_candle: bool = False,
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
            qty=qty,
            exit_reason=exit_reason,
            hold_candles=hold_candles,
            closed_at_step=closed_at_step,
            entry_price=entry_price,
            exit_price=exit_price,
            ambiguous_candle=ambiguous_candle,
        )

    def _maybe_skip_spot_short(
        self,
        *,
        signal: ExecuteSignalRequest,
        signal_entry: Decimal,
        step_index: int,
    ) -> SimulationExecutionResult | None:
        if self._market_mode != "spot" or signal.direction != "short":
            return None
        return self._build_skipped_result(
            signal_entry=signal_entry,
            step_index=step_index,
            exit_reason="rejected_short_signal",
            rejected_short_signal=True,
        )

    def _prepare_execution(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> _PreparedExecution | SimulationExecutionResult:
        raw_entry_price = self._resolve_entry_price(signal=signal, future_candles=future_candles)
        entry_is_maker = self._maker_fill()
        entry_fee_rate = self._maker_fee_rate if entry_is_maker else self._taker_fee_rate
        entry_price = self._apply_spread(
            price=raw_entry_price,
            side=self._entry_side(signal.direction),
            bps=self._spread_bps / Decimal(2) if not entry_is_maker else Decimal(0),
        )
        stop = Decimal(str(signal.stop))
        target = Decimal(str(signal.target))
        risk = abs(entry_price - stop)
        qty = self._risk_amount / risk if risk else Decimal(0)
        if qty <= 0:
            return SimulationExecutionResult(
                realized_pnl=Decimal(0),
                fees_paid=Decimal(0),
                slippage=Decimal(0),
                exit_reason="timeout",
                hold_candles=0,
                closed_at_step=step_index,
                entry_price=entry_price,
                exit_price=entry_price,
            )
        spot_skip = self._maybe_skip_spot_constraints(
            signal=signal,
            step_index=step_index,
            entry_price=entry_price,
            qty=qty,
        )
        if spot_skip is not None:
            return spot_skip
        return _PreparedExecution(
            raw_entry_price=raw_entry_price,
            entry_price=entry_price,
            entry_fee_rate=entry_fee_rate,
            stop=stop,
            target=target,
            qty=qty,
        )

    def _maybe_skip_spot_constraints(
        self,
        *,
        signal: ExecuteSignalRequest,
        step_index: int,
        entry_price: Decimal,
        qty: Decimal,
    ) -> SimulationExecutionResult | None:
        if self._market_mode != "spot":
            return None
        entry_notional = entry_price * qty
        min_notional = self._resolve_min_notional(signal.symbol)
        if entry_notional < min_notional:
            return self._build_skipped_result(
                signal_entry=entry_price,
                step_index=step_index,
                exit_reason="min_notional",
                skipped_min_notional=True,
            )
        if entry_notional > self._virtual_equity:
            return self._build_skipped_result(
                signal_entry=entry_price,
                step_index=step_index,
                exit_reason="insufficient_capital",
                skipped_insufficient_capital=True,
            )
        return None

    def _evaluate_candle_exit(
        self,
        *,
        signal: ExecuteSignalRequest,
        candle: MarketCandle,
        index: int,
        step_index: int,
        prepared_execution: _PreparedExecution,
    ) -> SimulationExecutionResult | None:
        opened = Decimal(str(candle.open))
        high = Decimal(str(candle.high))
        low = Decimal(str(candle.low))
        hold_candles = index + 1
        closed_at_step = step_index + hold_candles
        is_long = signal.direction == "long"
        stop_hit = low <= prepared_execution.stop if is_long else high >= prepared_execution.stop
        target_hit = high >= prepared_execution.target if is_long else low <= prepared_execution.target
        if not stop_hit and not target_hit:
            return None
        if stop_hit:
            return self._build_stop_result(
                signal=signal,
                candle_open=opened,
                hold_candles=hold_candles,
                closed_at_step=closed_at_step,
                prepared_execution=prepared_execution,
                ambiguous_candle=target_hit,
            )
        return self._build_target_result(
            signal=signal,
            hold_candles=hold_candles,
            closed_at_step=closed_at_step,
            prepared_execution=prepared_execution,
        )

    def _build_stop_result(
        self,
        *,
        signal: ExecuteSignalRequest,
        candle_open: Decimal,
        hold_candles: int,
        closed_at_step: int,
        prepared_execution: _PreparedExecution,
        ambiguous_candle: bool,
    ) -> SimulationExecutionResult:
        raw_exit_price = self._resolve_stop_exit_price(
            stop=prepared_execution.stop,
            candle_open=candle_open,
            direction=signal.direction,
        )
        exit_price = self._apply_slippage(price=raw_exit_price, direction=signal.direction)
        return self._build_result(
            signal=signal,
            qty=prepared_execution.qty,
            raw_entry_price=prepared_execution.raw_entry_price,
            entry_price=prepared_execution.entry_price,
            entry_fee_rate=prepared_execution.entry_fee_rate,
            raw_exit_price=raw_exit_price,
            exit_price=exit_price,
            exit_fee_rate=self._taker_fee_rate,
            exit_reason="sl_hit",
            hold_candles=hold_candles,
            closed_at_step=closed_at_step,
            ambiguous_candle=ambiguous_candle,
        )

    def _build_target_result(
        self,
        *,
        signal: ExecuteSignalRequest,
        hold_candles: int,
        closed_at_step: int,
        prepared_execution: _PreparedExecution,
    ) -> SimulationExecutionResult:
        exit_is_maker = self._maker_fill()
        exit_fee_rate = self._maker_fee_rate if exit_is_maker else self._taker_fee_rate
        raw_exit_price = prepared_execution.target
        exit_price = self._apply_spread(
            price=raw_exit_price,
            side=self._exit_side(signal.direction),
            bps=self._spread_bps / Decimal(2) if not exit_is_maker else Decimal(0),
        )
        return self._build_result(
            signal=signal,
            qty=prepared_execution.qty,
            raw_entry_price=prepared_execution.raw_entry_price,
            entry_price=prepared_execution.entry_price,
            entry_fee_rate=prepared_execution.entry_fee_rate,
            raw_exit_price=raw_exit_price,
            exit_price=exit_price,
            exit_fee_rate=exit_fee_rate,
            exit_reason="tp_hit",
            hold_candles=hold_candles,
            closed_at_step=closed_at_step,
        )

    def _build_timeout_result(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
        prepared_execution: _PreparedExecution,
    ) -> SimulationExecutionResult:
        timeout_hold_candles = min(self._max_hold_candles, len(future_candles))
        raw_exit_price = Decimal(str(future_candles[timeout_hold_candles - 1].close))
        exit_price = self._apply_slippage(price=raw_exit_price, direction=signal.direction)
        return self._build_result(
            signal=signal,
            qty=prepared_execution.qty,
            raw_entry_price=prepared_execution.raw_entry_price,
            entry_price=prepared_execution.entry_price,
            entry_fee_rate=prepared_execution.entry_fee_rate,
            raw_exit_price=raw_exit_price,
            exit_price=exit_price,
            exit_fee_rate=self._taker_fee_rate,
            exit_reason="timeout",
            hold_candles=timeout_hold_candles,
            closed_at_step=step_index + timeout_hold_candles,
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

    def _build_skipped_result(
        self,
        *,
        signal_entry: Decimal,
        step_index: int,
        exit_reason: str,
        rejected_short_signal: bool = False,
        skipped_min_notional: bool = False,
        skipped_insufficient_capital: bool = False,
    ) -> SimulationExecutionResult:
        return SimulationExecutionResult(
            realized_pnl=Decimal(0),
            fees_paid=Decimal(0),
            slippage=Decimal(0),
            exit_reason=exit_reason,
            hold_candles=0,
            closed_at_step=step_index,
            entry_price=signal_entry,
            exit_price=signal_entry,
            status="skipped",
            rejected_short_signal=rejected_short_signal,
            skipped_min_notional=skipped_min_notional,
            skipped_insufficient_capital=skipped_insufficient_capital,
        )

    def _resolve_min_notional(self, symbol: str) -> Decimal:
        normalized_symbol = symbol.strip().upper()
        return self._min_notional_by_symbol.get(normalized_symbol, self._default_min_notional)

    def _resolve_stop_exit_price(
        self,
        *,
        stop: Decimal,
        candle_open: Decimal,
        direction: str,
    ) -> Decimal:
        if direction == "long" and candle_open < stop:
            return candle_open
        if direction == "short" and candle_open > stop:
            return candle_open
        return stop

    def _apply_spread(
        self,
        *,
        price: Decimal,
        side: ExecutionSide,
        bps: Decimal,
    ) -> Decimal:
        move = (price * bps) / Decimal(10000)
        if side == "buy":
            return price + move
        return price - move

    def _entry_side(self, direction: str) -> ExecutionSide:
        if direction == "long":
            return "buy"
        return "sell"

    def _exit_side(self, direction: str) -> ExecutionSide:
        if direction == "long":
            return "sell"
        return "buy"

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
