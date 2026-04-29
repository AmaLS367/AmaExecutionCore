export interface MonthlyPnlPoint {
  month: string;
  pnl: string;
  trades: number;
  gross_profit: string;
  gross_loss: string;
  win_rate: string | null;
  profit_factor: string | null;
  max_drawdown_pct: string | null;
}

export interface BacktestOosResult {
  type: string;
  profitable_window_rate?: number | null;
  walk_forward_days?: number | null;
  walk_forward_windows?: number | null;
}

export interface BacktestScenarioResult {
  name: string;
  passed: boolean;
  failure_reasons: string[];
  symbol?: string;
  engine?: string;
  family?: string;
  strategy?: string;
  interval?: string;
  lookback_days?: number;
  profile?: string;
  closed_trades?: number;
  winning_trades?: number;
  gross_profit?: string | null;
  gross_loss?: string | null;
  win_rate?: string | null;
  expectancy?: string | null;
  profit_factor?: string | null;
  total_pnl?: string | null;
  net_pnl?: string | null;
  max_drawdown?: string | null;
  max_drawdown_pct?: string | null;
  fees_paid?: string | null;
  slippage_paid?: string | null;
  rejected_short_signals?: number;
  skipped_min_notional?: number;
  skipped_insufficient_capital?: number;
  ambiguous_candles?: number;
  monthly_pnl?: MonthlyPnlPoint[];
  best_month?: MonthlyPnlPoint | null;
  worst_month?: MonthlyPnlPoint | null;
  oos_result?: BacktestOosResult | null;
  profitable_window_rate?: number | null;
  completed_cycles?: number;
  annualized_yield_pct?: number;
  fee_coverage_ratio?: number;
  net_pnl_usdt?: number;
  max_unrealized_drawdown_pct?: number;
}

export interface BacktestReportMetadata {
  report_format_version: number;
  limitations: string[];
  source_file?: string;
}

export interface BacktestReport {
  strategy_name: string;
  suite_name: string | null;
  mode: string;
  generated_at: string;
  all_passed: boolean;
  metadata: BacktestReportMetadata;
  scenarios: BacktestScenarioResult[];
  manifest?: string;
  fee_rate_per_side?: string;
  suite?: string | null;
  results?: BacktestScenarioResult[];
}

export interface BacktestReportAvailability {
  available: false;
  message: string;
}

export type BacktestLatestResponse = BacktestReport | BacktestReportAvailability;
