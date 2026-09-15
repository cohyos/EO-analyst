import { useState } from "react";
import { ChevronUp, Loader2, WifiOff } from "lucide-react";
import type { StatusSocketState } from "@/hooks/useStatusSocket";
import { useResourceHistory } from "@/hooks/useResourceHistory";
import { ResourceHistoryDrawer } from "./ResourceHistoryDrawer";
import { cn } from "@/lib/cn";
import { useI18n } from "@/i18n";
import { jobLabel } from "@/lib/displayLabels";
import { stageLabelHe } from "@/lib/pipelineTimeline";

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
    <div
      className="flex shrink-0 items-center gap-1.5 whitespace-nowrap sm:min-w-[7.5rem] sm:gap-2"
      title={`${label}: ${used}/${total} ${unit}`}
    >
      <span className="text-fg-dim">{label}</span>
      {/* Meter track: secondary on a phone (defect — RAM/GPU strip cramped
          against the "local inference paused" banner on narrow widths) —
          hidden below `sm:` so only the compact label + percentage remain. */}
      <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-bg-sunken sm:block">
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
  searxng: "Search",
  ntfy: "ntfy",
};

export function StatusStrip({ state }: { state: StatusSocketState }) {
  const { status, connected } = state;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const history = useResourceHistory(status);
  const { t, locale } = useI18n();

  // Q5-9 (docs/qa/findings_Q5_r1.md): these are two distinct situations that used to render the
  // identical "מנותק מהשרת" banner -- (a) the WS socket itself is closed/erroring (`!connected`,
  // real disconnect, actively retrying with backoff -- see `useStatusSocket`), vs. (b) the socket
  // just opened (`connected` is already true) but hasn't delivered its first status snapshot yet
  // (`status` is still null), which on a cold page load reliably takes a couple of seconds and is
  // not a disconnect at all. Showing "מנותק" for (b) reads as a false alarm every single load.
  if (!connected) {
    return (
      <footer
        data-testid="status-strip-disconnected"
        className="pb-safe flex min-h-9 shrink-0 items-center gap-2 border-t border-border bg-bg-raised px-4 text-xs text-danger"
        role="status"
      >
        <WifiOff size={14} aria-hidden="true" />
        <span>מנותק מהשרת — מנסה להתחבר מחדש…</span>
      </footer>
    );
  }

  if (!status) {
    return (
      <footer
        data-testid="status-strip-connecting"
        className="pb-safe flex min-h-9 shrink-0 items-center gap-2 border-t border-border bg-bg-raised px-4 text-xs text-fg-dim"
        role="status"
      >
        <Loader2 size={14} aria-hidden="true" className="animate-spin" />
        <span>מתחבר… ממתין לתמונת מצב ראשונה</span>
      </footer>
    );
  }

  const { gate, pipeline, services } = status;
  const ramUsedMb = Math.max(0, gate.ram.total_mb - gate.ram.free_mb);
  const cpuOffloadCount = gate.loaded_models.filter((m) => m.cpu_offload).length;

  return (
    <div className="relative shrink-0">
      {gate.local_inference_paused && (
        // Own full-width block row above the (horizontally-scrolling) resource
        // strip — never the same flex row, so it can never run under/behind it;
        // `break-words` keeps it wrapping to a second line on a phone instead
        // of overflowing.
        <div
          role="status"
          className="w-full break-words border-t border-border bg-bg-raised px-4 py-1 text-xs leading-snug text-warn"
        >
          {t("shell.localInferencePaused")}
        </div>
      )}
      {drawerOpen && (
        <ResourceHistoryDrawer gate={gate} history={history} onClose={() => setDrawerOpen(false)} />
      )}
      <footer
        role="status"
        aria-label="סטטוס משאבים — לחץ להיסטוריה"
        className="pb-safe flex min-h-9 items-center gap-2 overflow-x-auto border-t border-border bg-bg-raised px-4 font-mono text-xs no-scrollbar-x sm:gap-4"
      >
        <button
          type="button"
          onClick={() => setDrawerOpen((v) => !v)}
          aria-expanded={drawerOpen}
          // Round-3 mobile fix (UI-MOBILE-iphone-r3.md #4): the strip itself must keep its compact
          // visual height (a taller footer eats into content on a phone), but the toggle's actual
          // tap target was only the ~20px-tall row. Padding grows the button's own hit area
          // (padding box) to >=40px tall; an equal-and-opposite negative vertical margin cancels
          // that growth back out of the flex row's cross-axis sizing (outer/margin-box size), so
          // the footer's height and the neighboring meters/readouts in this same row don't shift.
          className="flex shrink-0 items-center gap-2 rounded px-1 py-2.5 -my-2 hover:bg-bg-sunken"
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
          <span className="hidden text-fg-dim sm:inline">
            {mbToGb(gate.gpu.vram_used_mb)}/{mbToGb(gate.gpu.vram_total_mb)} GB
          </span>
          <span className="shrink-0 whitespace-nowrap text-fg-dim">GPU {gate.gpu.util_pct}%</span>
          {/* Temperature is diagnostic detail, not one of the load-bearing phone readouts —
              moved off the phone line (`hidden sm:inline`) so it can never be the thing that
              pushes RAM/queue/services past 390px; still available at `sm:`+ and in the drawer. */}
          <span
            className={cn(
              "hidden shrink-0 whitespace-nowrap sm:inline",
              gate.gpu.temp_c > 80 ? "text-hot" : "text-fg-dim",
            )}
          >
            {gate.gpu.temp_c}°C
          </span>
          <Meter label="RAM" used={ramUsedMb} total={gate.ram.total_mb} unit="MB" />
          <span className="hidden text-fg-dim sm:inline">דיסק {gate.disk_free_gb} GB פנוי</span>

          {/* Loaded-model chips and the CPU-offload badge are unbounded in width (model
              names, counts) — hidden below `sm:` so they can never overflow the phone strip. */}
          {gate.loaded_models.map((m) => (
            <span
              key={m.name}
              className={cn(
                "hidden shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 sm:inline-block",
                m.cpu_offload ? "bg-warn/15 text-warn" : "bg-bg-sunken text-accent",
              )}
              title={m.cpu_offload ? `${m.name} — CPU offload פעיל` : m.name}
            >
              {m.name}
              {m.cpu_offload && " ⚠"}
            </span>
          ))}
          {cpuOffloadCount > 0 && (
            <span className="hidden shrink-0 whitespace-nowrap rounded bg-warn/15 px-1.5 py-0.5 text-warn sm:inline-block">
              CPU offload ×{cpuOffloadCount}
            </span>
          )}
        </button>

        <span className="shrink-0 whitespace-nowrap text-fg-dim">תור: {pipeline.queue_depth}</span>
        {pipeline.stage && (
          <span className="hidden shrink-0 whitespace-nowrap rounded bg-accent-muted px-1.5 py-0.5 text-accent-fg sm:inline-block">
            {locale === "he" ? stageLabelHe(pipeline.stage) : pipeline.stage}
          </span>
        )}
        {pipeline.current_job && (
          // U4/F17: a persistent indicator that *something* is running in the background even
          // when the analyst isn't on the Morning page watching the run-now popover — F17's
          // repro was a job nobody could see anywhere in the UI. On phones the job's kind label
          // is unbounded width (Hebrew job names can run long), so it moves into the resource
          // drawer's reach at `sm:`+ rather than risk pushing the always-visible readouts off
          // a 390px screen; RAM/GPU/VRAM/queue/services stay the guaranteed-safe phone set.
          <span
            role="status"
            className="hidden shrink-0 items-center gap-1 whitespace-nowrap rounded bg-accent-muted px-1.5 py-0.5 text-accent-fg sm:flex"
            title={t("topBar.backgroundRunIndicator", { kind: jobLabel(pipeline.current_job.kind, locale) })}
          >
            <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-fg opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-fg" />
            </span>
            {jobLabel(pipeline.current_job.kind, locale)}
          </span>
        )}

        <div className="flex-1" />

        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          {/* Below `sm:` this collapses to dots-only (defect #1): the text label is hidden and
              the status moves onto the dot's own aria-label/title instead, so a screen reader
              or tooltip still gets "PG — מחובר" even though nothing is rendered as visible text. */}
          {Object.entries(services).map(([key, ok]) => {
            const label = SERVICE_LABELS[key] ?? key;
            const statusHe = ok ? "מחובר" : "מנותק";
            return (
              <span
                key={key}
                className="flex items-center gap-1"
                title={`${label} — ${statusHe}`}
                aria-label={`${label} — ${statusHe}`}
              >
                <span
                  className={cn("h-2 w-2 shrink-0 rounded-full", ok ? "bg-ok" : "bg-danger")}
                  aria-hidden="true"
                />
                <span className="hidden text-fg-dim sm:inline">{label}</span>
              </span>
            );
          })}
        </div>
      </footer>
    </div>
  );
}
