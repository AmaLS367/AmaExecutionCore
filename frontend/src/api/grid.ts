import { apiClient } from "./client";
import type { GridSession, GridSessionDetail } from "../types/grid";

export const gridApi = {
  getSessions: () =>
    apiClient.get<GridSession[]>("/admin/grid/sessions").then((r) => r.data),

  getSession: (id: number) =>
    apiClient
      .get<GridSessionDetail>(`/admin/grid/sessions/${id}`)
      .then((r) => r.data),
};
