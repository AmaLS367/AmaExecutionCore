import { apiClient } from "./client";
import type { BacktestLatestResponse } from "../types/backtestReport";

export const backtestReportsApi = {
  getLatest: () =>
    apiClient
      .get<BacktestLatestResponse>("/admin/backtest/reports/latest")
      .then((r) => r.data),
};
