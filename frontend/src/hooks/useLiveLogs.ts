import { useEffect, useState, useRef } from "react";
import { useAuthStore } from "../store/authStore";

export interface LogEntry {
  timestamp: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  module: string;
  message: string;
}

export function useLiveLogs() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const token = useAuthStore((s) => s.accessToken);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!token) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/admin/ws/logs?token=${token}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs((prev) => [...prev, data].slice(-1000));
      } catch (e) {
        console.error("Error parsing log:", e);
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [token]);

  const clearLogs = () => setLogs([]);

  return { logs, isConnected, clearLogs };
}
