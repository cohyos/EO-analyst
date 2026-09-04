import { useState } from "react";
import { ChevronUp, WifiOff } from "lucide-react";
import type { StatusSocketState } from "@/hooks/useStatusSocket";
import { useResourceHistory } from "@/hooks/useResourceHistory";
import { ResourceHistoryDrawer } from "./ResourceHistoryDrawer";
import { cn } from "@/lib/cn";

function mbToGb(mb: number): string {
  return (mb / 1024).toFixed(1);
}

function Meter({
  label,
  used,
  total,
  unit,
  danger,
}: {
  label: string;
  used: number;
  total: number;
  unit: string;
  danger?: boolean;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className="flex min-w-[7.5rem] items-center gap-2" title={`${label}: ${used}/${total} ${unit}`}>
      <span className="text-fg-dim">{label}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-bg-sunken">
        <div
          className={cn("h-full rounded-full", danger && pct > 85 ? "bg-hot" : "bg-accent")}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono font-tabular text-fg-muted">{pct}%</span>
    </div>
  );
}

const SERVICE_LABELS: Record<string, string> = {
  postgres: "PG",
  ollama: "Ollama",
  searxng: "SearXNG",
  ntfy: "ntfy",
};

export function StatusStrip({ state }: { state: StatusSocketState }) {
  const { status, connected } = state;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const history = useResourceHistory(status);

  if (!connected || !status) {
    return (
      <footer
        className="flex h-9 shrink-0 items-center gap-2 border-t border-border bg-bg-raised px-4 text-xs text-danger"
        role="status"
      >
        <WifiOff size={14} aria-hidden="true" />
        <span>מנותק מהשרת — מנסה להתחבר מחדש…</span>
      </footer>
    );
  }

  const { gate, pipeline, services } = status;
  const ramUsedMb = Math.max(0, gate.ram.total_mb - gate.ram.free_mb);
  const cpuOffloadCount = gate.loaded_models.filter((m) => m.cpu_offload).length;

  return (
    <div className="relative shrink-0">
      {drawerOpen && (
        <ResourceHistoryDrawer gate={gate} history={history} onClose={() => setDrawerOpen(false)} />
      )}
      <footer
        role="status"
        aria-label="סטטוס משאבים — לחץ להיסטוריה"
        className="flex h-9 items-center gap-4 overflow-x-auto border-t border-border bg-bg-raised px-4 font-mono text-xs no-scrollbar-x"
      >
        <button
          type="button"
          onClick={() => setDrawerOpen((v) => !v)}
          aria-expanded={drawerOpen}
          className="flex shrink-0 items-center gap-2 rounded px-1 py-0.5 hover:bg-bg-sunken"
          title="הצג/הסתר היסטוריית משאבים"
        >
          <ChevronUp
            size={12}
            aria-hidden="true"
            className={cn("transition-transform", drawerOpen && "rotate-180")}
          />
          <Meter
            label="VRAM"
            used={gate.gpu.vram_used_mb}
            total={gate.gpu.vram_total_mb}
            unit="MB"
            danger
          />
          <span className="text-fg-dim">
            {mbToGb(gate.gpu.vram_used_mb)}/{mbToGb(gate.gpu.vram_total_mb)} GB
          </span>
          <span className="text-fg-dim">GPU {gate.gpu.util_pct}%</span>
          <span className={cn(gate.gpu.temp_c > 80 ? "text-hot" : "text-fg-dim")}>
            {gate.gpu.temp_c}°C
          </span>
          <Meter label="RAM" used={ramUsedMb} total={gate.ram.total_mb} unit="MB" />
          <span className="text-fg-dim">דיסק {gate.disk_free_gb} GB פנוי</span>

          {gate.loaded_models.map((m) => (
            <span
              key={m.name}
              className={cn(
                "rounded px-1.5 py-0.5",
                m.cpu_offload ? "bg-warn/15 text-warn" : "bg-bg-sunken text-accent",
              )}
              title={m.cpu_offload ? `${m.name} — CPU offload פעיל` : m.name}
            >
              {m.name}
              {m.cpu_offload && " ⚠"}
            </span>
          ))}
          {cpuOffloadCount > 0 && (
            <span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">
              CPU offload ×{cpuOffloadCount}
            </span>
          )}
        </button>

        <span className="text-fg-dim">תור: {pipeline.queue_depth}</span>
        {pipeline.stage && (
          <span className="rounded bg-accent-muted px-1.5 py-0.5 text-accent-fg">
            {pipeline.stage}
          </span>
        )}

        <div className="flex-1" />

        <div className="flex items-center gap-2">
          {Object.entries(services).map(([key, ok]) => (
            <span key={key} className="flex items-center gap-1" title={SERVICE_LABELS[key] ?? key}>
              <span
                className={cn("h-2 w-2 rounded-full", ok ? "bg-ok" : "bg-danger")}
                aria-hidden="true"
              />
              <span className="text-fg-dim">{SERVICE_LABELS[key] ?? key}</span>
            </span>
          ))}
        </div>
      </footer>
    </div>
  );
}
