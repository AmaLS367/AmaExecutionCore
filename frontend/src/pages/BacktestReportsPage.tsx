import { useQuery } from "@tanstack/react-query";
import { backtestReportsApi } from "../api/backtestReports";
import type {
  BacktestLatestResponse,
  BacktestScenarioResult,
} from "../types/backtestReport";
import {
  formatCount,
  formatCurrency,
  formatProfitFactor,
  formatWinRate,
} from "../utils/adminFormatters";

type AggregatedMonthlyPoint = {
  month: string;
  pnl: number;
  trades: number;
  wins: number;
  grossProfit: number;
  grossLoss: number;
};

export function BacktestReportsPage() {
  const query = useQuery({
    queryKey: ["backtest-report-latest"],
    queryFn: backtestReportsApi.getLatest,
  });

  if (query.isLoading) {
    return (
      <PageState
        title="Backtest Reports"
        message="Loading latest backtest report..."
      />
    );
  }

  if (query.isError) {
    return (
      <PageState
        title="Backtest Reports"
        message="Failed to load backtest report."
        detail={query.error instanceof Error ? query.error.message : undefined}
      />
    );
  }

  if (!query.data || isAvailabilityResponse(query.data)) {
    return (
      <PageState
        title="Backtest Reports"
        message={query.data?.message ?? "No backtest report found."}
      />
    );
  }

  const report = query.data;
  const scenarios = report.scenarios;
  const tradeScenarios = scenarios.filter(isTradeScenario);
  const failedScenarios = scenarios.filter((scenario) => !scenario.passed);
  const passedCount = scenarios.filter((scenario) => scenario.passed).length;
  const aggregatedMonthly = aggregateMonthlySeries(tradeScenarios);
  const bestMonth = aggregatedMonthly.length
    ? aggregatedMonthly.reduce((best, current) => (current.pnl > best.pnl ? current : best))
    : null;
  const worstMonth = aggregatedMonthly.length
    ? aggregatedMonthly.reduce((worst, current) => (current.pnl < worst.pnl ? current : worst))
    : null;

  const summary = {
    scenarios: scenarios.length,
    passed: passedCount,
    failed: scenarios.length - passedCount,
    totalTrades: sumNumberField(tradeScenarios, "closed_trades"),
    averageProfitFactor: aggregateProfitFactor(tradeScenarios),
    totalPnl: sumNumericStringField(tradeScenarios, "total_pnl"),
    totalFees: sumNumericStringField(tradeScenarios, "fees_paid"),
    totalSlippage: sumNumericStringField(tradeScenarios, "slippage_paid"),
    rejectedShorts: sumNumberField(tradeScenarios, "rejected_short_signals"),
    skippedMinNotional: sumNumberField(tradeScenarios, "skipped_min_notional"),
    skippedInsufficientCapital: sumNumberField(tradeScenarios, "skipped_insufficient_capital"),
    ambiguousCandles: sumNumberField(tradeScenarios, "ambiguous_candles"),
  };

  return (
    <div className="space-y-6">
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Backtest Reports</p>
            <h1 className="text-2xl font-semibold text-zinc-100">{report.strategy_name}</h1>
            <div className="flex flex-wrap gap-3 text-sm text-zinc-400">
              <span>Suite: {report.suite_name ?? "—"}</span>
              <span>Mode: {report.mode}</span>
              <span>Generated: {new Date(report.generated_at).toLocaleString()}</span>
            </div>
          </div>
          <div className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${
            report.all_passed
              ? "bg-emerald-500/10 text-emerald-400"
              : "bg-red-500/10 text-red-400"
          }`}>
            {report.all_passed ? "PASS" : "FAIL"}
          </div>
        </div>
        {!!report.metadata.limitations.length && (
          <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">Limitations</p>
            <ul className="space-y-1 text-sm text-amber-100">
              {report.metadata.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
        <SummaryCard title="Scenarios" value={summary.scenarios.toString()} />
        <SummaryCard title="Passed" value={summary.passed.toString()} />
        <SummaryCard title="Failed" value={summary.failed.toString()} />
        <SummaryCard title="Total Trades" value={formatCount(summary.totalTrades)} />
        <SummaryCard title="Avg Profit Factor" value={formatProfitFactor(summary.averageProfitFactor)} />
        <SummaryCard title="Total P&L" value={formatCurrency(summary.totalPnl)} accent={summary.totalPnl !== null && summary.totalPnl < 0 ? "text-red-400" : "text-emerald-400"} />
        <SummaryCard title="Total Fees" value={formatCurrency(summary.totalFees)} />
        <SummaryCard title="Total Slippage" value={formatCurrency(summary.totalSlippage)} />
        <SummaryCard title="Rejected Shorts" value={formatCount(summary.rejectedShorts)} />
        <SummaryCard title="Skipped Min Notional" value={formatCount(summary.skippedMinNotional)} />
        <SummaryCard title="Skipped Capital" value={formatCount(summary.skippedInsufficientCapital)} />
        <SummaryCard title="Ambiguous Candles" value={formatCount(summary.ambiguousCandles)} />
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-zinc-800">
          <h2 className="text-lg font-medium">Scenario Results</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-zinc-400 uppercase bg-zinc-900/50 border-b border-zinc-800">
              <tr>
                <th className="px-6 py-3">Scenario</th>
                <th className="px-6 py-3">Symbol</th>
                <th className="px-6 py-3">TF</th>
                <th className="px-6 py-3 text-right">Trades</th>
                <th className="px-6 py-3 text-right">Win Rate</th>
                <th className="px-6 py-3 text-right">PF</th>
                <th className="px-6 py-3 text-right">Expectancy</th>
                <th className="px-6 py-3 text-right">Max DD</th>
                <th className="px-6 py-3 text-right">P&L</th>
                <th className="px-6 py-3 text-right">Fees</th>
                <th className="px-6 py-3 text-right">Slippage</th>
                <th className="px-6 py-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((scenario) => (
                <tr key={scenario.name} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                  <td className="px-6 py-4 font-medium text-zinc-100">{scenario.name}</td>
                  <td className="px-6 py-4">{scenario.symbol ?? "—"}</td>
                  <td className="px-6 py-4">{scenario.interval ?? "—"}</td>
                  <td className="px-6 py-4 text-right">{formatCount(scenario.closed_trades)}</td>
                  <td className="px-6 py-4 text-right">{formatWinRate(scenario.win_rate)}</td>
                  <td className="px-6 py-4 text-right">{formatProfitFactor(scenario.profit_factor)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(scenario.expectancy)}</td>
                  <td className="px-6 py-4 text-right">{formatWinRate(scenario.max_drawdown_pct)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(scenario.total_pnl ?? scenario.net_pnl)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(scenario.fees_paid)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(scenario.slippage_paid)}</td>
                  <td className="px-6 py-4">
                    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                      scenario.passed
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-red-500/10 text-red-400"
                    }`}>
                      {scenario.passed ? "PASS" : "FAIL"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-zinc-800">
            <h2 className="text-lg font-medium">Monthly P&L</h2>
          </div>
          {!aggregatedMonthly.length ? (
            <div className="px-6 py-10 text-sm text-zinc-500">
              No trade monthly data available for this report.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="text-xs text-zinc-400 uppercase bg-zinc-900/50 border-b border-zinc-800">
                  <tr>
                    <th className="px-6 py-3">Month</th>
                    <th className="px-6 py-3 text-right">P&L</th>
                    <th className="px-6 py-3 text-right">Trades</th>
                    <th className="px-6 py-3 text-right">Win Rate</th>
                    <th className="px-6 py-3 text-right">PF</th>
                  </tr>
                </thead>
                <tbody>
                  {aggregatedMonthly.map((point) => (
                    <tr key={point.month} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                      <td className="px-6 py-4 font-medium text-zinc-100">{point.month}</td>
                      <td className={`px-6 py-4 text-right ${point.pnl < 0 ? "text-red-400" : "text-emerald-400"}`}>
                        {formatCurrency(point.pnl)}
                      </td>
                      <td className="px-6 py-4 text-right">{point.trades}</td>
                      <td className="px-6 py-4 text-right">
                        {point.trades > 0 ? `${((point.wins / point.trades) * 100).toFixed(1)}%` : "—"}
                      </td>
                      <td className="px-6 py-4 text-right">{formatProfitFactor(profitFactorFromGross(point.grossProfit, point.grossLoss))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="space-y-6">
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
            <h2 className="text-lg font-medium mb-4">Best / Worst Month</h2>
            <div className="space-y-3 text-sm">
              <MetricRow label="Best Month" value={bestMonth ? `${bestMonth.month} (${formatCurrency(bestMonth.pnl)})` : "—"} />
              <MetricRow label="Worst Month" value={worstMonth ? `${worstMonth.month} (${formatCurrency(worstMonth.pnl)})` : "—"} />
            </div>
          </div>

          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
            <h2 className="text-lg font-medium mb-4">Failure Reasons</h2>
            {!failedScenarios.length ? (
              <p className="text-sm text-zinc-500">No failed scenarios.</p>
            ) : (
              <div className="space-y-4">
                {failedScenarios.map((scenario) => (
                  <div key={scenario.name} className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
                    <p className="font-medium text-red-300">{scenario.name}</p>
                    <ul className="mt-2 space-y-1 text-sm text-red-100">
                      {scenario.failure_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function isAvailabilityResponse(data: BacktestLatestResponse): data is { available: false; message: string } {
  return "available" in data && data.available === false;
}

function isTradeScenario(scenario: BacktestScenarioResult): boolean {
  return typeof scenario.closed_trades === "number";
}

function parseNumericString(value: string | null | undefined): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (value === "Infinity") {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function sumNumericStringField(
  scenarios: BacktestScenarioResult[],
  field: "total_pnl" | "fees_paid" | "slippage_paid",
): number | null {
  let total = 0;
  let hasValue = false;
  for (const scenario of scenarios) {
    const value = parseNumericString(scenario[field]);
    if (value === null || value === Number.POSITIVE_INFINITY) {
      continue;
    }
    total += value;
    hasValue = true;
  }
  return hasValue ? total : null;
}

function sumNumberField(
  scenarios: BacktestScenarioResult[],
  field:
    | "closed_trades"
    | "rejected_short_signals"
    | "skipped_min_notional"
    | "skipped_insufficient_capital"
    | "ambiguous_candles",
): number | null {
  let total = 0;
  let hasValue = false;
  for (const scenario of scenarios) {
    const value = scenario[field];
    if (typeof value !== "number") {
      continue;
    }
    total += value;
    hasValue = true;
  }
  return hasValue ? total : null;
}

function aggregateProfitFactor(scenarios: BacktestScenarioResult[]): number | null {
  let grossProfit = 0;
  let grossLoss = 0;
  let hasValue = false;

  for (const scenario of scenarios) {
    const scenarioGrossProfit = parseNumericString(scenario.gross_profit);
    const scenarioGrossLoss = parseNumericString(scenario.gross_loss);
    if (scenarioGrossProfit === null || scenarioGrossLoss === null) {
      continue;
    }
    grossProfit += scenarioGrossProfit;
    grossLoss += scenarioGrossLoss;
    hasValue = true;
  }

  if (!hasValue) {
    return null;
  }
  return profitFactorFromGross(grossProfit, grossLoss);
}

function profitFactorFromGross(grossProfit: number, grossLoss: number): number | null {
  if (grossLoss === 0) {
    return grossProfit === 0 ? null : Number.POSITIVE_INFINITY;
  }
  return grossProfit / grossLoss;
}

function aggregateMonthlySeries(scenarios: BacktestScenarioResult[]): AggregatedMonthlyPoint[] {
  const byMonth = new Map<string, AggregatedMonthlyPoint>();
  for (const scenario of scenarios) {
    for (const point of scenario.monthly_pnl ?? []) {
      const pnl = parseNumericString(point.pnl);
      const winRate = parseNumericString(point.win_rate);
      const bucket = byMonth.get(point.month) ?? {
        month: point.month,
        pnl: 0,
        trades: 0,
        wins: 0,
        grossProfit: 0,
        grossLoss: 0,
      };
      const grossProfit = parseNumericString(point.gross_profit);
      const grossLoss = parseNumericString(point.gross_loss);
      if (pnl !== null && pnl !== Number.POSITIVE_INFINITY) {
        bucket.pnl += pnl;
      }
      bucket.trades += point.trades;
      if (winRate !== null) {
        bucket.wins += winRate * point.trades;
      }
      if (grossProfit !== null && grossProfit !== Number.POSITIVE_INFINITY) {
        bucket.grossProfit += grossProfit;
      }
      if (grossLoss !== null && grossLoss !== Number.POSITIVE_INFINITY) {
        bucket.grossLoss += grossLoss;
      }
      byMonth.set(point.month, bucket);
    }
  }
  return Array.from(byMonth.values()).sort((left, right) => left.month.localeCompare(right.month));
}

function SummaryCard({ title, value, accent = "text-zinc-100" }: { title: string; value: string; accent?: string }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <p className="text-xs font-medium text-zinc-400 mb-1">{title}</p>
      <p className={`text-xl font-bold ${accent}`}>{value}</p>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-zinc-400">{label}</span>
      <span className="text-right text-zinc-100">{value}</span>
    </div>
  );
}

function PageState({ title, message, detail }: { title: string; message: string; detail?: string }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-8 text-center">
      <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">{title}</p>
      <h1 className="mt-3 text-2xl font-semibold text-zinc-100">{message}</h1>
      {detail ? <p className="mt-2 text-sm text-zinc-500">{detail}</p> : null}
    </div>
  );
}
