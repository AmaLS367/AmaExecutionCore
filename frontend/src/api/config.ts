import { apiClient } from "./client";

export const configApi = {
  get: () =>
    apiClient.get<Record<string, unknown>>("/admin/config").then((r) => r.data),

  reload: () =>
    apiClient
      .post<{ ok: boolean; message: string }>("/admin/config/reload")
      .then((r) => r.data),
};
