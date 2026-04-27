import { apiClient } from "./client";

export interface LoginResponse {
  totp_required: boolean;
  session_token: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export const authApi = {
  login: (username: string, password: string) =>
    apiClient
      .post<LoginResponse>("/admin/auth/login", { username, password })
      .then((r) => r.data),

  verifyTotp: (sessionToken: string, totpCode: string) =>
    apiClient
      .post<TokenResponse>("/admin/auth/verify-totp", {
        session_token: sessionToken,
        totp_code: totpCode,
      })
      .then((r) => r.data),

  refresh: () =>
    apiClient
      .post<TokenResponse>("/admin/auth/refresh")
      .then((r) => r.data),

  logout: () => apiClient.post("/admin/auth/logout"),
};
