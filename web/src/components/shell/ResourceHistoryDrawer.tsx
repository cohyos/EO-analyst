import { AlertTriangle, X } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import type { GateDecision, GateDecisionKind, LoadedModel, ResourceGateStatus } from "@/types/api";
import type { ResourceSample } from "@/hooks/useResourceHistory";
import { cn } from "@/lib/cn";
import { formatTime } from "@/lib/time";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const DECISION_META: Record<GateDecisionKind, { labelKey: TranslationKey; className: string }> = {
  proceed: { labelKey: "shell.decisionProceed", className: "bg-ok/15 text-ok" },
  queued: { labelKey: "shell.decisionQueued", className: "bg-accent-muted text-accent" },
  deferred: { labelKey: "shell.decisionDeferred", className: "bg-warn/15 text-warn" },
  swap: { labelKey: "shell.decisionSwap", className: "bg-accent-muted text-accent" },
  throttled: { labelKey: "shell.decisionThrottled", className: "bg-warn/15 text-warn" },
  thermal_pause: { labelKey: "shell.decisionThermalPause", className: "bg-danger/15 text-danger" },
};

function Sparkline({
  data,
  dataKey,
  unit,
  color,
  domain,
  formatValue,
}: {
  data: ResourceSample[];
  dataKey: keyof ResourceSample;
  unit: string;
  color: string;
  domain?: [number | string, number | string];
  formatValue?: (v: number) => string;
}) {
  const t = useT();
  const latest = data.length > 0 ? data[data.length - 1][dataKey] : undefined;
  return (
    <div className="rounded-lg border border-border bg-bg-sunken p-2">
      <div className="mb-1 flex items-center justify-between text-xs text-fg-dim">
        <span>{unit}</span>
        <span className="font-mono font-tabular text-fg">
          {latest != null ? (formatValue ? formatValue(latest) : String(latest)) : "—"}
        </span>
      </div>
      <div className="h-16 w-full" dir="ltr">
        {data.length < 2 ? (
          <div className="flex h-full items-center justify-center text-xs text-fg-dim">
            {t("shell.resourceHistoryCollectingData")}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
              <YAxis hide domain={domain ?? ["auto", "auto"]} />
              <Tooltip
                labelFormatter={(v) => formatTime(new Date(v as number).toISOString())}
                formatter={(v: number) => [formatValue ? formatValue(v) : v, unit]}
                contentStyle={{
                  background: "var(--bg-raised)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 11,
                }}
              />
              <Area
                type="monotone"
                dataKey={dataKey}
                stroke={color}
                fill={color}
                fillOpacity={0.25}
                strokeWidth={1.5}
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function LoadedModelChip({ model }: { model: LoadedModel }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
        model.cpu_offload
          ? "border-warn/40 bg-warn/10 text-warn"
          : "border-border-strong bg-bg-sunken text-fg-dim",
      )}
      title={`${model.name} — ${(model.size_mb / 1024).toFixed(1)} GB (VRAM: ${(model.size_vram_mb / 1024).toFixed(1)} GB)`}
    >
      {model.cpu_offload && <AlertTriangle size={12} aria-hidden="true" />}
      <bdi className="max-w-[14rem] truncate font-mono">{model.name}</bdi>
      {model.cpu_offload && <span className="whitespace-nowrap">CPU offload</span>}
    </span>
  );
}

function DecisionRow({ decision }: { decision: GateDecision }) {
  const t = useT();
  const meta = DECISION_META[decision.decision];
  const label = meta ? t(meta.labelKey) : decision.decision;
  const className = meta?.className ?? "bg-bg-sunken text-fg-dim";
  return (
    <li className="flex items-start gap-2 border-b border-border py-1.5 text-xs last:border-b-0">
      <span className="shrink-0 font-mono text-fg-dim">{formatTime(decision.at)}</span>
      <span className={cn("shrink-0 rounded px-1.5 py-0.5 font-medium", className)}>{label}</span>
      <bdi className="min-w-0 flex-1 truncate font-mono text-fg-muted" title={decision.model}>
        {decision.model}
      </bdi>
      <bdi className="min-w-0 max-w-[40%] truncate text-fg-dim" dir="auto" title={decision.reason}>
        {decision.reason}
      </bdi>
    </li>
  );
}

export function ResourceHistoryDrawer({
  gate,
  history,
  onClose,
}: {
  gate: ResourceGateStatus;
  history: ResourceSample[];
  onClose: () => void;
}) {
  const t = useT();
  const decisions = [...gate.recent_decisions].reverse();
  return (
    <div
      role="dialog"
      aria-label={t("shell.resourceHistoryDialogAria")}
      className="absolute inset-x-0 bottom-9 z-20 max-h-[70vh] overflow-y-auto border-t border-border bg-bg-raised p-4 shadow-panel"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">{t("shell.resourceHistoryTitle")}</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("shell.closeAria")}
          className="tap-target inline-flex items-center justify-center rounded-md p-1 text-fg-dim hover:bg-bg-sunken hover:text-fg"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Sparkline
          data={history}
          dataKey="vram_used_mb"
          unit={t("shell.resourceHistoryVram")}
          color="var(--hot)"
          domain={[0, gate.gpu.vram_total_mb || "auto"]}
          formatValue={(v) => `${Math.round(v)}`}
        />
        <Sparkline
          data={history}
          dataKey="gpu_util_pct"
          unit={t("shell.resourceHistoryGpuUtil")}
          color="var(--accent)"
          domain={[0, 100]}
          formatValue={(v) => `${Math.round(v)}%`}
        />
        <Sparkline
          data={history}
          dataKey="gpu_temp_c"
          unit={t("shell.resourceHistoryGpuTemp")}
          color="var(--danger)"
          formatValue={(v) => `${Math.round(v)}°`}
        />
        <Sparkline
          data={history}
          dataKey="ram_free_mb"
          unit={t("shell.resourceHistoryRamFree")}
          color="var(--ok)"
          domain={[0, gate.ram.total_mb || "auto"]}
          formatValue={(v) => `${Math.round(v)}`}
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section aria-label={t("shell.resourceHistoryLoadedModelsAria")}>
          <h4 className="mb-2 text-xs font-semibold text-fg-dim">{t("shell.resourceHistoryLoadedModelsTitle")}</h4>
          {gate.loaded_models.length === 0 ? (
            <p className="text-xs text-fg-dim">{t("shell.resourceHistoryNoLoadedModels")}</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {gate.loaded_models.map((m) => (
                <LoadedModelChip key={m.name} model={m} />
              ))}
            </div>
          )}
        </section>

        <section aria-label={t("shell.resourceHistoryDecisionsAria")}>
          <h4 className="mb-2 text-xs font-semibold text-fg-dim">{t("shell.resourceHistoryDecisionsTitle")}</h4>
          {decisions.length === 0 ? (
            <p className="text-xs text-fg-dim">{t("shell.resourceHistoryNoDecisions")}</p>
          ) : (
            <ol className="max-h-40 overflow-y-auto">
              {decisions.map((d, i) => (
                <DecisionRow key={`${d.at}-${i}`} decision={d} />
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
