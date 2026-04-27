import axios from "axios";
import { useAuthStore } from "../store/authStore";

export const apiClient = axios.create({
  baseURL: "/api",
  withCredentials: true,
  headers: {
    "X-Requested-With": "XMLHttpRequest",
  },
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  
  const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
  if (csrfMatch) {
    config.headers["X-CSRF-Token"] = csrfMatch[1];
  }
  
  return config;
});

let _isRefreshing = false;
let _refreshSubscribers: Array<(token: string) => void> = [];

function _onRefreshed(token: string) {
  _refreshSubscribers.forEach((cb) => cb(token));
  _refreshSubscribers = [];
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (
      error.response?.status !== 401 ||
      originalRequest._retry ||
      originalRequest.url?.includes("/admin/auth/refresh")
    ) {
      return Promise.reject(error);
    }

    if (_isRefreshing) {
      return new Promise((resolve) => {
        _refreshSubscribers.push((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          resolve(apiClient(originalRequest));
        });
      });
    }

    originalRequest._retry = true;
    _isRefreshing = true;

    try {
      const { data } = await apiClient.post<{ access_token: string }>(
        "/admin/auth/refresh"
      );
      useAuthStore.getState().setToken(data.access_token);
      _onRefreshed(data.access_token);
      originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
      return apiClient(originalRequest);
    } catch {
      useAuthStore.getState().clearToken();
      return Promise.reject(error);
    } finally {
      _isRefreshing = false;
    }
  }
);
