import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import MockAdapter from "axios-mock-adapter";
import { MemoryRouter } from "react-router-dom";
import { BacktestReportsPage } from "./BacktestReportsPage";
import { apiClient } from "../api/client";

let mock: MockAdapter;

beforeEach(() => {
  mock = new MockAdapter(apiClient);
});

afterEach(() => {
  mock.restore();
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <BacktestReportsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BacktestReportsPage", () => {
  it("renders empty state when no report exists", async () => {
    mock.onGet("/admin/backtest/reports/latest").reply(200, {
      available: false,
      message: "No backtest report found.",
    });

    renderPage();

    expect(await screen.findByText("No backtest report found.")).toBeTruthy();
  });

  it("renders failed scenario reasons and visible counters", async () => {
    mock.onGet("/admin/backtest/reports/latest").reply(200, {
      strategy_name: "regime_grid_v1",
      suite_name: "regime_grid_gate",
      mode: "regression",
      generated_at: "2026-04-29T00:00:00+00:00",
      all_passed: false,
      metadata: {
        report_format_version: 2,
        limitations: [],
      },
      scenarios: [
        {
          name: "btc_validation",
          strategy: "vwap_reversion",
          symbol: "BTCUSDT",
          interval: "15",
          passed: false,
          failure_reasons: ["profit_factor 0.8 < 1.2"],
          closed_trades: 10,
          win_rate: "0.5",
          profit_factor: "0.8",
          expectancy: "-1.25",
          total_pnl: "-12.5",
          fees_paid: "3.5",
          slippage_paid: "1.25",
          max_drawdown_pct: "0.08",
          rejected_short_signals: 2,
          skipped_min_notional: 3,
          skipped_insufficient_capital: 4,
          ambiguous_candles: 1,
          monthly_pnl: [
            {
              month: "2026-01",
              pnl: "-12.5",
              trades: 10,
              win_rate: "0.5",
              profit_factor: "0.8",
              max_drawdown_pct: "0.08",
            },
          ],
          best_month: null,
          worst_month: {
            month: "2026-01",
            pnl: "-12.5",
            trades: 10,
            win_rate: "0.5",
            profit_factor: "0.8",
            max_drawdown_pct: "0.08",
          },
          oos_result: null,
        },
      ],
    });

    renderPage();

    expect(await screen.findByText("profit_factor 0.8 < 1.2")).toBeTruthy();
    expect(screen.getByText("Rejected Shorts")).toBeTruthy();
    expect(screen.getByText("Skipped Min Notional")).toBeTruthy();
    expect(screen.getByText("Skipped Capital")).toBeTruthy();
    expect(screen.getByText("Ambiguous Candles")).toBeTruthy();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);
  });
});
