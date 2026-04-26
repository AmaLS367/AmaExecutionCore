import { describe, it, expect, beforeEach } from "vitest";
import { useAuthStore } from "../authStore";

beforeEach(() => {
  useAuthStore.getState().clearToken();
});

describe("authStore — initial state", () => {
  it("is not authenticated when no token is set", () => {
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("has null access token initially", () => {
    expect(useAuthStore.getState().accessToken).toBeNull();
  });
});

describe("authStore — setToken", () => {
  it("stores the access token", () => {
    useAuthStore.getState().setToken("tok-abc");
    expect(useAuthStore.getState().accessToken).toBe("tok-abc");
  });

  it("marks user as authenticated after token is set", () => {
    useAuthStore.getState().setToken("tok-abc");
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
});

describe("authStore — clearToken", () => {
  it("removes the access token", () => {
    useAuthStore.getState().setToken("tok-abc");
    useAuthStore.getState().clearToken();
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("marks user as unauthenticated after clear", () => {
    useAuthStore.getState().setToken("tok-abc");
    useAuthStore.getState().clearToken();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
