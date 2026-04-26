import { describe, it, expect, beforeEach, afterEach } from "vitest";
import axios from "axios";
import MockAdapter from "axios-mock-adapter";
import { apiClient } from "../client";
import { useAuthStore } from "../../store/authStore";

let mock: MockAdapter;

beforeEach(() => {
  mock = new MockAdapter(apiClient);
  useAuthStore.getState().clearToken();
});

afterEach(() => {
  mock.restore();
});

describe("apiClient — Authorization header injection", () => {
  it("sends Authorization header when token is present", async () => {
    useAuthStore.getState().setToken("test-access-token");
    mock.onGet("/admin/stats/dashboard").reply(200, { equity: 10000 });

    const response = await apiClient.get("/admin/stats/dashboard");

    expect(response.config.headers?.["Authorization"]).toBe(
      "Bearer test-access-token"
    );
  });

  it("sends no Authorization header when no token is set", async () => {
    mock.onGet("/admin/stats/dashboard").reply(200, {});

    const response = await apiClient.get("/admin/stats/dashboard");

    expect(response.config.headers?.["Authorization"]).toBeUndefined();
  });
});

describe("apiClient — 401 refresh flow", () => {
  it("calls refresh endpoint on 401 and retries the original request", async () => {
    useAuthStore.getState().setToken("expired-token");

    mock
      .onPost("/admin/auth/refresh")
      .reply(200, { access_token: "new-token" });

    let callCount = 0;
    mock.onGet("/admin/trades").reply(() => {
      callCount++;
      if (callCount === 1) return [401, { detail: "Unauthorized" }];
      return [200, { items: [] }];
    });

    const response = await apiClient.get("/admin/trades");

    expect(response.status).toBe(200);
    expect(useAuthStore.getState().accessToken).toBe("new-token");
  });

  it("clears token and rejects when refresh itself fails", async () => {
    useAuthStore.getState().setToken("expired-token");

    mock.onPost("/admin/auth/refresh").reply(401, {});
    mock.onGet("/admin/trades").reply(401, { detail: "Unauthorized" });

    await expect(apiClient.get("/admin/trades")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
