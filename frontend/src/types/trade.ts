export interface Trade {
  id: string;
  symbol: string;
  signal_direction: "long" | "short";
  exchange_side: "Buy" | "Sell";
  market_type: string;
  mode: string;
  status: string;
  realized_pnl: number | null;
  closed_at: string | null;
  created_at: string | null;
}

export interface TradeListResponse {
  total: number;
  page: number;
  pages: number;
  items: Trade[];
}
