export interface DashboardStats {
  equity: number;
  trading_mode: string;
  safety_guard_status: "OK" | "PAUSED" | "KILLED";
  open_positions_count: number;
  pnl_today: number;
}

export interface EquityPoint {
  date: string;
  equity: number;
}

export interface DailyPnlPoint {
  date: string;
  pnl: number;
}

export interface TradeSummary {
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  total_pnl: number;
  avg_trade: number;
}
