import { useEffect, useState } from "react";
import type { InvestigationLogLine } from "@/types/api";
import { USE_MOCKS } from "@/api";

export function useInvestigationSocket(jobId: string | undefined, isRunning: boolean) {
  const [liveLines, setLiveLines] = useState<InvestigationLogLine[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    setLiveLines([]);

    if (USE_MOCKS) {
      setConnected(true);
      if (!isRunning) return;
      let round = 3;
      const interval = setInterval(() => {
        round += 1;
        setLiveLines((prev) => [
          ...prev,
          {
            round,
            lang: round % 2 === 0 ? "en" : "he",
            query: `שאילתת המשך #${round} (מדומה)`,
            results: Math.floor(Math.random() * 10) + 1,
            outcome: "מנתח תוצאות…",
            at: new Date().toISOString(),
          },
        ]);
      }, 4000);
      return () => clearInterval(interval);
    }

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/investigations/${jobId}`);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (evt) => {
      try {
        const line = JSON.parse(evt.data) as InvestigationLogLine;
        setLiveLines((prev) => [...prev, line]);
      } catch {
        // ignore malformed frame
      }
    };
    return () => ws.close();
  }, [jobId, isRunning]);

  return { liveLines, connected };
}
