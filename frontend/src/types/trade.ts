export interface Trade {
  id: string;
  symbol: string;
  signal_direction: "long" | "short";
  exchange_side: "Buy" | "Sell";
  market_type: string;
  mode: string;
  status: string;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  qty: number | null;
  expected_rrr: number | null;
  risk_amount_usd: number | null;
  risk_pct: number | null;
  realized_pnl: number | null;
  opened_at: string | null;
  closed_at: string | null;
  created_at: string | null;
}

export interface TradeListResponse {
  total: number;
  page: number;
  pages: number;
  items: Trade[];
}
