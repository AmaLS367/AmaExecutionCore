import { apiClient } from "./client";
import type { Trade, TradeListResponse } from "../types/trade";

export const tradesApi = {
  list: (params?: {
    symbol?: string;
    from_date?: string;
    to_date?: string;
    page?: number;
    limit?: number;
  }) =>
    apiClient
      .get<TradeListResponse>("/admin/trades", { params })
      .then((r) => r.data),

  getOpen: () =>
    apiClient.get<Trade[]>("/admin/trades/open").then((r) => r.data),
};
