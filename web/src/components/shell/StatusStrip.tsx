import { WifiOff } from "lucide-react";
import type { StatusSocketState } from "@/hooks/useStatusSocket";
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

  return (
    <footer
      className="flex h-9 shrink-0 items-center gap-4 overflow-x-auto border-t border-border bg-bg-raised px-4 font-mono text-xs no-scrollbar-x"
      role="status"
      aria-label="סטטוס משאבים"
    >
      <Meter
        label="VRAM"
        used={gate.vram_used_mb}
        total={gate.vram_total_mb}
        unit="MB"
        danger
      />
      <span className="text-fg-dim">
        {mbToGb(gate.vram_used_mb)}/{mbToGb(gate.vram_total_mb)} GB
      </span>
      <span className="text-fg-dim">GPU {gate.gpu_util_pct}%</span>
      <span className={cn(gate.gpu_temp_c > 80 ? "text-hot" : "text-fg-dim")}>
        {gate.gpu_temp_c}°C
      </span>
      <Meter label="RAM" used={gate.ram_used_mb} total={gate.ram_total_mb} unit="MB" />
      <span className="text-fg-dim">דיסק {gate.disk_free_gb} GB פנוי</span>

      {gate.loaded_model && (
        <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-accent">
          {gate.loaded_model}
        </span>
      )}

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
  );
}
