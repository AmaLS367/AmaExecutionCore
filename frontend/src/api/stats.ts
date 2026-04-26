import { apiClient } from "./client";
import type { DashboardStats, EquityPoint, DailyPnlPoint, TradeSummary } from "../types/stats";

export const statsApi = {
  getDashboard: () =>
    apiClient.get<DashboardStats>("/admin/stats/dashboard").then((r) => r.data),

  getEquityCurve: (days = 30) =>
    apiClient
      .get<EquityPoint[]>("/admin/stats/equity-curve", { params: { days } })
      .then((r) => r.data),

  getDailyPnl: (days = 30) =>
    apiClient
      .get<DailyPnlPoint[]>("/admin/stats/daily-pnl", { params: { days } })
      .then((r) => r.data),

  getSummary: () =>
    apiClient.get<TradeSummary>("/admin/trades/summary").then((r) => r.data),
};
