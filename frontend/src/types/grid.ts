export interface GridSession {
  id: number;
  symbol: string;
  status: string;
  config: Record<string, unknown>;
  created_at: string | null;
  stopped_at: string | null;
}

export interface GridSlot {
  id: number;
  level: number;
  buy_price: number;
  sell_price: number;
  status: string;
  completed_cycles: number;
  realized_pnl: number;
}

export interface GridSessionDetail extends GridSession {
  slots: GridSlot[];
}
