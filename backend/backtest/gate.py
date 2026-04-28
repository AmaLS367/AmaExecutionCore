from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from backend.backtest.metrics import calculate_max_drawdown
from backend.backtest.replay_runner import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SupportsReplayExecutionContext,
    SupportsReplayStrategy,
)
from backend.backtest.simulation_execution_service import (
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.factory import build_day_trading_strategy, build_scalping_strategy

StrategyFamily = Literal["day_trading", "scalping"]


@dataclass(slots=True, frozen=True)
class BacktestThresholdProfile:
    name: str
    min_closed_trades: int
    min_win_rate: Decimal
    min_profit_factor: Decimal
    require_positive_expectancy: bool
    max_drawdown_pct: Decimal


@dataclass(slots=True, frozen=True)
class BacktestScenario:
    name: str
    family: StrategyFamily
    strategy: str
    symbol: str
    interval: str
    lookback_days: int
    live_lookback_days: int
    dataset_path: str
    risk_amount_usd: float
    starting_equity_usd: float
    max_hold_candles: int
    min_rrr: float
    regression_profile: str
    live_profile: str


@dataclass(slots=True, frozen=True)
class BacktestManifest:
    profiles: dict[str, BacktestThresholdProfile]
    scenarios: tuple[BacktestScenario, ...]


@dataclass(slots=True, frozen=True)
class ScenarioMetrics:
    closed_trades: int
    winning_trades: int
    win_rate: Decimal | None
    expectancy: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    max_drawdown_pct: Decimal | None
    net_pnl: Decimal
    fees_paid: Decimal
    rejected_short_signals: int
    skipped_min_notional: int
    skipped_insufficient_capital: int
    ambiguous_candles: int


@dataclass(slots=True, frozen=True)
class ScenarioEvaluation:
    name: str
    family: StrategyFamily
    strategy: str
    symbol: str
    interval: str
    lookback_days: int
    profile: str
    metrics: ScenarioMetrics
    passed: bool
    failure_reasons: tuple[str, ...]


def load_manifest(path: Path) -> BacktestManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Backtest manifest must be a JSON object.")

    raw_profiles = payload.get("profiles")
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_profiles, dict) or not isinstance(raw_scenarios, list):
        raise TypeError("Backtest manifest must define profiles and scenarios.")

    profiles = {
        profile_name: BacktestThresholdProfile(
            name=profile_name,
            min_closed_trades=int(profile_payload["min_closed_trades"]),
            min_win_rate=Decimal(str(profile_payload["min_win_rate"])),
            min_profit_factor=Decimal(str(profile_payload["min_profit_factor"])),
            require_positive_expectancy=bool(profile_payload["require_positive_expectancy"]),
            max_drawdown_pct=Decimal(str(profile_payload["max_drawdown_pct"])),
        )
        for profile_name, profile_payload in raw_profiles.items()
        if isinstance(profile_payload, dict)
    }
    scenarios = tuple(
        BacktestScenario(
            name=str(item["name"]),
            family=cast("StrategyFamily", str(item["family"])),
            strategy=str(item["strategy"]),
            symbol=str(item["symbol"]).strip().upper(),
            interval=str(item["interval"]).strip(),
            lookback_days=int(item["lookback_days"]),
            live_lookback_days=int(item["live_lookback_days"]),
            dataset_path=str(item["dataset_path"]),
            risk_amount_usd=float(item.get("risk_amount_usd", 100.0)),
            starting_equity_usd=float(item.get("starting_equity_usd", 10_000.0)),
            max_hold_candles=int(item.get("max_hold_candles", 20)),
            min_rrr=float(item.get("min_rrr", 1.5)),
            regression_profile=str(item["regression_profile"]),
            live_profile=str(item["live_profile"]),
        )
        for item in raw_scenarios
        if isinstance(item, dict)
    )
    if not profiles or not scenarios:
        raise ValueError("Backtest manifest must define at least one profile and one scenario.")
    for scenario in scenarios:
        if scenario.regression_profile not in profiles:
            raise ValueError(f"Unknown regression profile: {scenario.regression_profile}")
        if scenario.live_profile not in profiles:
            raise ValueError(f"Unknown live profile: {scenario.live_profile}")
    return BacktestManifest(profiles=profiles, scenarios=scenarios)


async def evaluate_scenario(
    *,
    scenario: BacktestScenario,
    candles: tuple[MarketCandle, ...],
    profile: BacktestThresholdProfile,
    fee_rate_per_side: float,
    lookback_days: int | None = None,
) -> ScenarioEvaluation:
    strategy = _build_strategy(scenario)
    simulation_service = SimulationExecutionService(
        max_hold_candles=scenario.max_hold_candles,
        risk_amount_usd=scenario.risk_amount_usd,
        fee_rate_per_side=fee_rate_per_side,
        market_mode="spot",
        virtual_equity_usd=scenario.starting_equity_usd,
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=cast("SupportsReplayStrategy", strategy),
        execution_service=cast(
            "SupportsReplayExecutionContext[SimulationExecutionResult]",
            simulation_service,
        ),
    )

    replay_result = await runner.replay(
        HistoricalReplayRequest(
            symbol=scenario.symbol,
            interval=scenario.interval,
            candles=candles,
        ),
    )

    metrics = _calculate_metrics(
        scenario=scenario,
        executions=tuple(
            step.execution
            for step in replay_result.steps
            if step.execution is not None
        ),
        report=replay_result.report,
    )
    failure_reasons = _evaluate_profile(metrics=metrics, profile=profile)
    return ScenarioEvaluation(
        name=scenario.name,
        family=scenario.family,
        strategy=scenario.strategy,
        symbol=scenario.symbol,
        interval=scenario.interval,
        lookback_days=lookback_days or scenario.lookback_days,
        profile=profile.name,
        metrics=metrics,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
    )


def serialize_evaluation(evaluation: ScenarioEvaluation) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": evaluation.name,
        "family": evaluation.family,
        "strategy": evaluation.strategy,
        "symbol": evaluation.symbol,
        "interval": evaluation.interval,
        "lookback_days": evaluation.lookback_days,
        "profile": evaluation.profile,
        "passed": evaluation.passed,
        "failure_reasons": list(evaluation.failure_reasons),
    }
    for key, value in asdict(evaluation.metrics).items():
        payload[key] = str(value) if isinstance(value, Decimal) else value
    return payload


def _build_strategy(scenario: BacktestScenario) -> object:
    if scenario.family == "day_trading":
        return build_day_trading_strategy(
            strategy_name=scenario.strategy,
            min_rrr=scenario.min_rrr,
        )
    if scenario.family == "scalping":
        return build_scalping_strategy(
            strategy_name=scenario.strategy,
            min_rrr=scenario.min_rrr,
        )
    raise ValueError(f"Unsupported strategy family: {scenario.family}")


def _calculate_metrics(
    *,
    scenario: BacktestScenario,
    executions: tuple[SimulationExecutionResult, ...],
    report: object,
) -> ScenarioMetrics:
    net_trade_pnls = tuple(
        execution.realized_pnl - execution.fees_paid
        for execution in executions
    )
    closed_trades = len(net_trade_pnls)
    winning_trades = sum(1 for pnl in net_trade_pnls if pnl > 0)
    gross_wins = sum((pnl for pnl in net_trade_pnls if pnl > 0), Decimal(0))
    gross_losses = sum((abs(pnl) for pnl in net_trade_pnls if pnl < 0), Decimal(0))
    net_pnl = sum(net_trade_pnls, Decimal(0))
    fees_paid = sum((execution.fees_paid for execution in executions), Decimal(0))

    win_rate = None
    expectancy = None
    if closed_trades:
        win_rate = Decimal(winning_trades) / Decimal(closed_trades)
        expectancy = net_pnl / Decimal(closed_trades)

    profit_factor: Decimal | None
    if gross_losses == 0:
        profit_factor = None if gross_wins == 0 else Decimal("Infinity")
    else:
        profit_factor = gross_wins / gross_losses

    max_drawdown = calculate_max_drawdown(net_trade_pnls)
    max_drawdown_pct = None
    if scenario.starting_equity_usd > 0:
        max_drawdown_pct = max_drawdown / Decimal(str(scenario.starting_equity_usd))

    return ScenarioMetrics(
        closed_trades=closed_trades,
        winning_trades=winning_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        net_pnl=net_pnl,
        fees_paid=fees_paid,
        rejected_short_signals=getattr(report.counters, "rejected_short_signals", 0),
        skipped_min_notional=getattr(report.counters, "skipped_min_notional", 0),
        skipped_insufficient_capital=getattr(report.counters, "skipped_insufficient_capital", 0),
        ambiguous_candles=getattr(report.counters, "ambiguous_candles", 0),
    )

def _evaluate_profile(
    *,
    metrics: ScenarioMetrics,
    profile: BacktestThresholdProfile,
) -> tuple[str, ...]:
    failure_reasons: list[str] = []
    if metrics.closed_trades < profile.min_closed_trades:
        failure_reasons.append(
            f"closed_trades {metrics.closed_trades} < {profile.min_closed_trades}",
        )
    if metrics.win_rate is None or metrics.win_rate < profile.min_win_rate:
        failure_reasons.append(
            f"win_rate {metrics.win_rate} < {profile.min_win_rate}",
        )
    if metrics.profit_factor is None or metrics.profit_factor < profile.min_profit_factor:
        failure_reasons.append(
            f"profit_factor {metrics.profit_factor} < {profile.min_profit_factor}",
        )
    if profile.require_positive_expectancy and (
        metrics.expectancy is None or metrics.expectancy <= Decimal(0)
    ):
        failure_reasons.append(
            f"expectancy {metrics.expectancy} <= 0",
        )
    if metrics.max_drawdown_pct is None or metrics.max_drawdown_pct > profile.max_drawdown_pct:
        failure_reasons.append(
            f"max_drawdown_pct {metrics.max_drawdown_pct} > {profile.max_drawdown_pct}",
        )
    return tuple(failure_reasons)
