from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.backtest.gate import (
    BacktestScenario,
    BacktestThresholdProfile,
    MonthlyPnlPoint,
    ScenarioEvaluation,
    ScenarioMetrics,
    SimulationExecutionResult,
    evaluate_scenario,
    load_manifest,
    serialize_evaluation,
)
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal


def _build_manifest(path: Path) -> None:
    path.write_text(
        """
{
  "profiles": {
    "regression_v1": {
      "min_closed_trades": 1,
      "min_win_rate": 0.5,
      "min_profit_factor": 1.0,
      "require_positive_expectancy": true,
      "max_drawdown_pct": 0.2
    }
  },
  "scenarios": [
    {
      "name": "btc_vwap",
      "family": "scalping",
      "strategy": "vwap_reversion",
      "symbol": "BTCUSDT",
      "interval": "5",
      "lookback_days": 365,
      "live_lookback_days": 180,
      "dataset_path": "scripts/fixtures/regression/btcusdt_5m_365d.json.gz",
      "risk_amount_usd": 100.0,
      "starting_equity_usd": 10000.0,
      "max_hold_candles": 20,
      "min_rrr": 1.5,
      "regression_profile": "regression_v1",
      "live_profile": "regression_v1"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )


def _candles() -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index * 5),
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=1000.0 + index,
        )
        for index in range(5)
    )


def test_load_manifest_parses_profiles_and_scenarios(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _build_manifest(manifest_path)

    manifest = load_manifest(manifest_path)

    assert tuple(manifest.profiles) == ("regression_v1",)
    assert manifest.scenarios[0].family == "scalping"
    assert manifest.scenarios[0].symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_evaluate_scenario_uses_scalping_strategy_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _build_manifest(manifest_path)
    manifest = load_manifest(manifest_path)
    scenario = manifest.scenarios[0]
    profile = BacktestThresholdProfile(
        name="regression_v1",
        min_closed_trades=0,
        min_win_rate=0,
        min_profit_factor=0,
        require_positive_expectancy=False,
        max_drawdown_pct=1,
    )
    called: list[tuple[str, float]] = []

    class _Strategy:
        required_candle_count = 1

        async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
            del snapshot
            return StrategySignal(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=99.0,
                target=101.0,
            )

    def _build_scalping_strategy(*, strategy_name: str, min_rrr: float) -> _Strategy:
        called.append((strategy_name, min_rrr))
        return _Strategy()

    monkeypatch.setattr("backend.backtest.gate.build_scalping_strategy", _build_scalping_strategy)

    evaluation = await evaluate_scenario(
        scenario=scenario,
        candles=_candles(),
        profile=profile,
        fee_rate_per_side=0.001,
        lookback_days=scenario.live_lookback_days,
    )

    assert called == [("vwap_reversion", 1.5)]
    serialized = serialize_evaluation(evaluation)
    assert serialized["strategy"] == "vwap_reversion"
    assert serialized["lookback_days"] == 180
    assert "win_rate" in serialized


def test_serialize_evaluation_includes_spot_execution_counters() -> None:
    january = MonthlyPnlPoint(
        month="2024-01",
        pnl=Decimal(10),
        trades=1,
        gross_profit=Decimal(12),
        gross_loss=Decimal(0),
        win_rate=Decimal(1),
        profit_factor=Decimal(2),
        max_drawdown_pct=Decimal("0.01"),
    )
    evaluation = ScenarioEvaluation(
        name="btc_vwap",
        family="scalping",
        strategy="vwap_reversion",
        symbol="BTCUSDT",
        interval="5",
        lookback_days=180,
        profile="regression_v1",
        metrics=ScenarioMetrics(
            closed_trades=1,
            winning_trades=1,
            gross_profit=Decimal(12),
            gross_loss=Decimal(0),
            win_rate=Decimal(1),
            expectancy=Decimal(10),
            profit_factor=Decimal(2),
            max_drawdown=Decimal(1),
            max_drawdown_pct=Decimal("0.01"),
            total_pnl=Decimal(12),
            net_pnl=Decimal(10),
            fees_paid=Decimal(1),
            slippage_paid=Decimal("0.25"),
            rejected_short_signals=2,
            skipped_min_notional=3,
            skipped_insufficient_capital=4,
            ambiguous_candles=5,
            monthly_pnl=(january,),
            best_month=january,
            worst_month=january,
            oos_result=None,
        ),
        passed=True,
        failure_reasons=(),
    )

    serialized = serialize_evaluation(evaluation)

    assert serialized["rejected_short_signals"] == 2
    assert serialized["skipped_min_notional"] == 3
    assert serialized["skipped_insufficient_capital"] == 4
    assert serialized["ambiguous_candles"] == 5
    assert serialized["slippage_paid"] == "0.25"
    assert serialized["monthly_pnl"] == [
        {
            "month": "2024-01",
            "pnl": "10",
            "trades": 1,
            "gross_profit": "12",
            "gross_loss": "0",
            "win_rate": "1",
            "profit_factor": "2",
            "max_drawdown_pct": "0.01",
        },
    ]
    assert serialized["best_month"] == serialized["monthly_pnl"][0]
    assert serialized["worst_month"] == serialized["monthly_pnl"][0]


def test_calculate_metrics_excludes_skipped_executions_from_trade_stats() -> None:
    from backend.backtest.gate import _calculate_metrics
    from backend.backtest.replay_runner import (
        HistoricalReplayCounters,
        HistoricalReplayMetrics,
        HistoricalReplayReport,
    )

    scenario = BacktestScenario(
        name="btc_vwap",
        family="scalping",
        strategy="vwap_reversion",
        symbol="BTCUSDT",
        interval="5",
        lookback_days=365,
        live_lookback_days=180,
        dataset_path="fixture.json",
        risk_amount_usd=100.0,
        starting_equity_usd=10_000.0,
        max_hold_candles=20,
        min_rrr=1.5,
        regression_profile="regression_v1",
        live_profile="regression_v1",
    )
    metrics, limitations = _calculate_metrics(
        scenario=scenario,
        candles=_candles(),
        executions=(
            SimulationExecutionResult(
                realized_pnl=Decimal(0),
                fees_paid=Decimal(0),
                slippage=Decimal(0),
                exit_reason="rejected_short",
                hold_candles=0,
                status="skipped",
                rejected_short_signal=True,
            ),
            SimulationExecutionResult(
                realized_pnl=Decimal(10),
                fees_paid=Decimal(1),
                slippage=Decimal("0.1"),
                exit_reason="tp_hit",
                hold_candles=1,
                status="closed",
            ),
        ),
        report=HistoricalReplayReport(
            metrics=HistoricalReplayMetrics(
                closed_trades=1,
                winning_trades=1,
                losing_trades=0,
                expectancy=Decimal(9),
                win_rate=Decimal(1),
                profit_factor=None,
                max_drawdown=Decimal(0),
            ),
            slippage=None,
            counters=HistoricalReplayCounters(rejected_short_signals=1),
        ),
    )

    assert metrics.closed_trades == 1
    assert metrics.winning_trades == 1
    assert metrics.gross_profit == Decimal(9)
    assert metrics.gross_loss == Decimal(0)
    assert metrics.net_pnl == Decimal(9)
    assert metrics.total_pnl == Decimal(10)
    assert metrics.fees_paid == Decimal(1)
    assert metrics.rejected_short_signals == 1
    assert metrics.monthly_pnl
    assert metrics.best_month is not None
    assert limitations == ()


def test_calculate_metrics_reports_monthly_timestamp_limitations() -> None:
    from backend.backtest.gate import _calculate_metrics
    from backend.backtest.replay_runner import (
        HistoricalReplayCounters,
        HistoricalReplayMetrics,
        HistoricalReplayReport,
    )

    scenario = BacktestScenario(
        name="btc_vwap",
        family="scalping",
        strategy="vwap_reversion",
        symbol="BTCUSDT",
        interval="5",
        lookback_days=365,
        live_lookback_days=180,
        dataset_path="fixture.json",
        risk_amount_usd=100.0,
        starting_equity_usd=10_000.0,
        max_hold_candles=20,
        min_rrr=1.5,
        regression_profile="regression_v1",
        live_profile="regression_v1",
    )

    metrics, limitations = _calculate_metrics(
        scenario=scenario,
        candles=_candles(),
        executions=(
            SimulationExecutionResult(
                realized_pnl=Decimal(10),
                fees_paid=Decimal(1),
                slippage=Decimal("0.2"),
                qty=Decimal(2),
                exit_reason="tp_hit",
                hold_candles=1,
                closed_at_step=999,
                status="closed",
            ),
        ),
        report=HistoricalReplayReport(
            metrics=HistoricalReplayMetrics(
                closed_trades=1,
                winning_trades=1,
                losing_trades=0,
                expectancy=Decimal(9),
                win_rate=Decimal(1),
                profit_factor=None,
                max_drawdown=Decimal(0),
            ),
            slippage=None,
            counters=HistoricalReplayCounters(),
        ),
    )

    assert metrics.monthly_pnl == ()
    assert metrics.best_month is None
    assert metrics.worst_month is None
    assert metrics.total_pnl == Decimal(10)
    assert limitations == (
        "btc_vwap: excluded 1 closed trades from monthly_pnl because close timestamps could not be resolved.",
    )


@pytest.mark.asyncio
async def test_evaluate_scenario_keeps_realistic_spot_execution_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _build_manifest(manifest_path)
    manifest = load_manifest(manifest_path)
    scenario = manifest.scenarios[0]
    profile = BacktestThresholdProfile(
        name="regression_v1",
        min_closed_trades=0,
        min_win_rate=0,
        min_profit_factor=0,
        require_positive_expectancy=False,
        max_drawdown_pct=Decimal(1),
    )
    init_kwargs: dict[str, object] = {}

    class _Strategy:
        required_candle_count = 1

        async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
            del snapshot
            return StrategySignal(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=99.0,
                target=101.0,
            )

    class _SimulationExecutionService:
        def __init__(self, **kwargs: object) -> None:
            init_kwargs.update(kwargs)

        async def execute_replay_signal(
            self,
            *,
            signal: object,
            future_candles: object,
            step_index: int,
        ) -> SimulationExecutionResult:
            del signal, future_candles
            return SimulationExecutionResult(
                realized_pnl=Decimal(10),
                fees_paid=Decimal(1),
                slippage=Decimal("0.1"),
                exit_reason="tp_hit",
                hold_candles=1,
                closed_at_step=step_index + 1,
                entry_price=Decimal(100),
                exit_price=Decimal(101),
            )

    monkeypatch.setattr("backend.backtest.gate.build_scalping_strategy", lambda **_: _Strategy())
    monkeypatch.setattr("backend.backtest.gate.SimulationExecutionService", _SimulationExecutionService)

    await evaluate_scenario(
        scenario=scenario,
        candles=_candles(),
        profile=profile,
        fee_rate_per_side=0.001,
        lookback_days=scenario.lookback_days,
    )

    assert init_kwargs["market_mode"] == "spot"
    assert init_kwargs["virtual_equity_usd"] == scenario.starting_equity_usd
    assert init_kwargs["fee_rate_per_side"] == 0.001
    assert "legacy_fee_shortcut" not in init_kwargs
