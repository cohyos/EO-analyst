import { useEffect, useState } from "react";
import type { StatusResponse, StatusWsMessage } from "@/types/api";
import { USE_MOCKS } from "@/api";
import { normalizeStatus } from "@/api/normalize";
import { mockStatus } from "@/mocks/data/misc";

export interface StatusSocketState {
  status: StatusResponse | null;
  connected: boolean;
  logs: Array<{ message: string; at: string; level?: string }>;
}

const MAX_LOGS = 30;

/**
 * Feeds the bottom resource-status strip from WS /ws/status (real mode) or
 * a simulated interval carrying the same shape (mock mode). Auto-reconnects
 * with backoff and surfaces a "disconnected" state while retrying.
 */
export function useStatusSocket(): StatusSocketState {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [connected, setConnected] = useState(false);
  const [logs, setLogs] = useState<StatusSocketState["logs"]>([]);

  useEffect(() => {
    let cancelled = false;

    if (USE_MOCKS) {
      setConnected(true);
      setStatus(normalizeStatus(mockStatus()));
      const interval = setInterval(() => {
        if (cancelled) return;
        setStatus(normalizeStatus(mockStatus()));
      }, 2000);
      const logInterval = setInterval(() => {
        if (cancelled) return;
        setLogs((prev) =>
          [
            {
              message: `[mock] סבב חיפוש הושלם — ${new Date().toLocaleTimeString("he-IL")}`,
              at: new Date().toISOString(),
              level: "info",
            },
            ...prev,
          ].slice(0, MAX_LOGS),
        );
      }, 9000);
      return () => {
        cancelled = true;
        clearInterval(interval);
        clearInterval(logInterval);
      };
    }

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const connect = () => {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/ws/status`);

      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data) as StatusWsMessage;
          if ("type" in data && data.type === "log") {
            setLogs((prev) =>
              [{ message: data.message, at: data.at, level: data.level }, ...prev].slice(
                0,
                MAX_LOGS,
              ),
            );
          } else {
            setStatus(normalizeStatus(data as Partial<StatusResponse>));
          }
        } catch {
          // ignore malformed frame
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        attempt += 1;
        const backoff = Math.min(1000 * 2 ** attempt, 15000);
        reconnectTimer = setTimeout(connect, backoff);
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

  return { status, connected, logs };
}
