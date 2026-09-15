import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Cloud, Cpu, Save } from "lucide-react";
import { api } from "@/api";
import { SETTINGS_NAMES, type LlmChainEntry, type SettingsName } from "@/types/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ChainsEditor } from "@/components/settings/ChainsEditor";
import { MCPCard } from "@/components/settings/MCPCard";
import { formatDateTime, formatDuration } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useI18n } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const PROVIDER_KIND_KEY: Record<"local" | "cloud" | "api", TranslationKey> = {
  local: "llm.providerKind.local",
  cloud: "llm.providerKind.cloud",
  api: "llm.providerKind.api",
};

const TAB_LABEL: Record<SettingsName, string> = {
  config: "config",
  sources: "sources",
  watchlist: "watchlist",
  taxonomy: "taxonomy",
  models: "models",
};

// W20 (docs/REVIEW_2026-09-06_evening.md): state label + colour, resolved through `t()`/a fixed
// Tailwind token per state rather than the plain flat string the table used to render.
const JOB_STATE_LABEL_KEY: Record<string, TranslationKey> = {
  queued: "settingsJobs.stateQueued",
  running: "settingsJobs.stateRunning",
  done: "settingsJobs.stateDone",
  failed: "settingsJobs.stateFailed",
  deferred: "settingsJobs.stateDeferred",
  partial: "settingsJobs.statePartial",
};

const JOB_STATE_COLOR: Record<string, string> = {
  queued: "text-fg-dim",
  running: "text-accent",
  done: "text-ok",
  failed: "text-danger",
  deferred: "text-fg-dim",
  partial: "text-warn",
};

export function SettingsPage() {
  const { t, locale } = useI18n();
  const [tab, setTab] = useState<SettingsName>("config");
  const [draft, setDraft] = useState("");
  const [saveResult, setSaveResult] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"eco" | "full">("full");

  const settingsQuery = useQuery({
    queryKey: ["settings", tab],
    queryFn: () => api.getSettings(tab),
  });

  useEffect(() => {
    if (settingsQuery.data) {
      setDraft(settingsQuery.data.yaml);
      setSaveResult(null);
    }
  }, [settingsQuery.data]);

  const save = useMutation({
    mutationFn: () => api.putSettings(tab, draft),
    onSuccess: (res) => {
      setSaveResult({ ok: res.ok, errors: res.errors ?? [] });
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["settings", tab] });
    },
    onError: (err: unknown) => {
      setSaveResult({
        ok: false,
        errors: [err instanceof Error ? err.message : "שגיאת שמירה לא ידועה"],
      });
    },
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.getJobs(undefined, 50),
    refetchInterval: 8000,
  });

  const cancelJob = useMutation({
    mutationFn: (id: number) => api.postJobCancel(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const runQuick = useMutation({
    mutationFn: (m: "eco" | "full") => api.postRun("daily", m),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const llmQuery = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => api.getLlmProviders(),
  });
  const [llmSaveResult, setLlmSaveResult] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const saveLlmDefault = useMutation({
    mutationFn: (interactive_default: string) => api.putLlmSettings({ interactive_default }),
    onSuccess: (res) => {
      setLlmSaveResult({ ok: res.ok, errors: res.errors });
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });
  const saveAllowCloud = useMutation({
    mutationFn: (allow_cloud: boolean) => api.putLlmSettings({ allow_cloud }),
    onSuccess: (res) => {
      setLlmSaveResult({ ok: res.ok, errors: res.errors });
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });
  // U8-א (Revision 2026-09-06): the global local/cloud switch — applies to chat AND the night
  // pipeline/queued jobs (classify/triage/analyze/deep search), unlike `interactive_default`
  // above, which is only the chat's own per-question default.
  const saveMode = useMutation({
    mutationFn: (mode: "local" | "cloud") => api.putLlmSettings({ mode }),
    onSuccess: (res) => {
      setLlmSaveResult({ ok: res.ok, errors: res.errors });
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });
  // U8-4: per-provider fallback-chain call accounting for the last 24h.
  const llmCallsQuery = useQuery({
    queryKey: ["llm-calls"],
    queryFn: () => api.getLlmCalls("24h"),
    refetchInterval: 30_000,
  });

  // U8-ה: the ChainsEditor's own save action — replaces the whole `llm_providers.chains` map.
  const [chainsSaveResult, setChainsSaveResult] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const saveChains = useMutation({
    mutationFn: (chains: Record<string, LlmChainEntry[]>) => api.putLlmSettings({ chains }),
    onSuccess: (res) => {
      setChainsSaveResult({ ok: res.ok, errors: res.errors });
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
    onError: (err: unknown) => {
      setChainsSaveResult({
        ok: false,
        errors: [err instanceof Error ? err.message : "שגיאת שמירה לא ידועה"],
      });
    },
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-6">
      <section aria-label="מודלים" className="rounded-lg border border-border bg-bg-raised p-3">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">מודלים</h2>
        {llmQuery.isLoading && <LoadingState label="טוען הגדרות מודלים…" />}
        {llmQuery.data && (
          <div className="space-y-3">
            {/* U8-א (Revision 2026-09-06): the GLOBAL local/cloud switch — applies everywhere,
                including the night pipeline and queued jobs, unlike the chat-only default below. */}
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-border-strong bg-bg p-2">
              <span className="text-sm font-medium text-fg">{t("llm.modeLabel")}:</span>
              <div className="flex items-center gap-1 rounded-md border border-border-strong p-0.5">
                {(["local", "cloud"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => saveMode.mutate(m)}
                    aria-pressed={llmQuery.data!.mode === m}
                    className={cn(
                      "flex items-center gap-1 rounded px-2.5 py-1 text-xs font-medium",
                      llmQuery.data!.mode === m ? "bg-accent text-accent-fg" : "text-fg-muted hover:bg-bg-sunken",
                    )}
                  >
                    {m === "local" ? <Cpu size={12} aria-hidden="true" /> : <Cloud size={12} aria-hidden="true" />}
                    {m === "local" ? t("llm.modeLocal") : t("llm.modeCloud")}
                  </button>
                ))}
              </div>
              <p className="w-full text-xs text-fg-dim">{t("llm.modeHint")}</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor="llm-default" className="text-sm text-fg-muted">
                מודל ברירת מחדל לצ׳אט ולחקירות אינטראקטיביות (עוקף את המצב הגלובלי, לשאלה זו בלבד):
              </label>
              {/* Mobile fix (UI-MOBILE-iphone.md #3): the model select's longest option
                  ("...gemini-3.1-pro-high") sized the box past the screen edge on phones with no
                  way to see or reach the rest of it. `max-w-full min-w-0` caps it to the wrapping
                  row's actual width. */}
              <select
                id="llm-default"
                defaultValue={llmQuery.data.interactive_default}
                onChange={(e) => saveLlmDefault.mutate(e.target.value)}
                className="w-full max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg sm:w-auto"
              >
                {llmQuery.data.providers
                  .filter((p) => p.kind === "local" || llmQuery.data!.allow_cloud)
                  .flatMap((p) =>
                    p.kind === "local"
                      ? [
                          <option key={p.id} value={p.id}>
                            {p.label}
                          </option>,
                        ]
                      : p.models.map((m) => (
                          <option key={`${p.id}:${m}`} value={`${p.id}:${m}`}>
                            {p.label} — {m}
                          </option>
                        )),
                  )}
              </select>
              <span className="flex items-center gap-1 text-xs text-fg-dim">
                {llmQuery.data.interactive_default === "ollama" ? (
                  <>
                    <Cpu size={12} aria-hidden="true" /> מקומי
                  </>
                ) : (
                  <>
                    <Cloud size={12} aria-hidden="true" /> ענן
                  </>
                )}
              </span>
            </div>

            <label className="flex items-center gap-2 text-sm text-fg-muted">
              <input
                type="checkbox"
                checked={llmQuery.data.allow_cloud}
                onChange={(e) => saveAllowCloud.mutate(e.target.checked)}
                className="h-4 w-4 rounded border-border-strong"
              />
              אפשר מודלי ענן (CLI/API) בכל המערכת
            </label>
            <p className="text-xs text-fg-dim">
              כיבוי המתג חוסם כל שימוש במודל ענן — גם בצ׳אט וגם במצב "ענן" הגלובלי, שני המקרים
              ייפלו תמיד למודל המקומי. שינוי מצב המודלים למעלה הוא זה שקובע האם הריצה הלילית
              ומשימות הרקע (סיווג/מיון/ניתוח, חקירת עומק, "הרץ עכשיו") משתמשות בענן או במקומי בלבד
              — ומהו הדגם הזמין הוא נגזרת של המתג הזה.
            </p>

            {/* U8-ה: per-role fallback-chain editor — previously chains could only be hand-edited
                in config.yaml via the raw YAML tab below. */}
            <ChainsEditor
              chains={llmQuery.data.chains}
              providers={llmQuery.data.providers}
              allowCloud={llmQuery.data.allow_cloud}
              onSave={(chains) => saveChains.mutate(chains)}
              saving={saveChains.isPending}
              saveResult={chainsSaveResult}
            />

            <ul className="space-y-1 text-xs text-fg-dim">
              {llmQuery.data.providers.map((p) => (
                <li key={p.id} className="flex items-center gap-1.5">
                  {p.kind === "local" ? (
                    <Cpu size={11} aria-hidden="true" />
                  ) : (
                    <Cloud size={11} aria-hidden="true" />
                  )}
                  <bdi>{p.label}</bdi>
                  <span className="text-fg-dim">({t(PROVIDER_KIND_KEY[p.kind])})</span>
                  {p.kind === "api" ? (
                    <span className={p.available ? "text-ok" : "text-fg-dim"}>
                      {p.available ? t("llm.keyConfigured") : t("llm.keyNotConfigured")}
                      {p.key_env ? ` (${p.key_env})` : ""}
                    </span>
                  ) : (
                    <span className={p.available ? "text-ok" : "text-danger"}>
                      {p.available ? "זמין" : "לא זמין"}
                    </span>
                  )}
                </li>
              ))}
            </ul>

            {/* U8-4: fallback-chain call accounting for the last 24h. */}
            {llmCallsQuery.data && (
              <p className="text-xs text-fg-dim">
                {llmCallsQuery.data.totals.calls > 0
                  ? t("llm.callsSummary", {
                      cloud: llmCallsQuery.data.totals.cloud_calls,
                      fallback: llmCallsQuery.data.totals.fallbacks,
                      cost: llmCallsQuery.data.totals.est_cost_usd.toFixed(4),
                    })
                  : t("llm.callsSummaryEmpty")}
              </p>
            )}

            {llmSaveResult && (
              <span className={cn("block text-sm", llmSaveResult.ok ? "text-ok" : "text-danger")}>
                {llmSaveResult.ok ? "נשמר בהצלחה" : `שגיאות: ${llmSaveResult.errors.join("; ")}`}
              </span>
            )}
          </div>
        )}
      </section>

      {/* Tablet+ (md, 768px): these two small cards share a row instead of
          each spanning the full max-w-4xl column — on a phone they still
          stack (flex-col). */}
      <div className="flex flex-col gap-6 md:grid md:grid-cols-2 md:items-start md:gap-4">
        <MCPCard />

        <section aria-label="בקרות מהירות" className="rounded-lg border border-border bg-bg-raised p-3">
          <h2 className="mb-2 text-sm font-semibold text-fg-dim">בקרות מהירות</h2>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-md border border-border-strong p-1">
              {(["eco", "full"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    "rounded px-2.5 py-1 text-xs font-medium",
                    mode === m ? "bg-accent text-accent-fg" : "text-fg-muted hover:bg-bg-sunken",
                  )}
                >
                  {m === "eco" ? "מצב חסכוני" : "מצב מלא"}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => runQuick.mutate(mode)}
              disabled={runQuick.isPending}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
            >
              {runQuick.isPending ? "מריץ…" : `הרץ ריצה יומית (${mode})`}
            </button>
          </div>
        </section>
      </div>

      <section aria-label="עריכת הגדרות YAML">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">עריכת קבצי הגדרה</h2>
        <div className="mb-2 flex gap-1 border-b border-border" role="tablist">
          {SETTINGS_NAMES.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              onClick={() => setTab(name)}
              className={cn(
                "rounded-t-md px-3 py-1.5 text-sm font-mono",
                tab === name
                  ? "border-x border-t border-border bg-bg-raised text-fg"
                  : "text-fg-dim hover:text-fg",
              )}
            >
              {TAB_LABEL[name]}
            </button>
          ))}
        </div>

        {settingsQuery.isLoading && <LoadingState label="טוען קובץ…" />}
        {settingsQuery.isError && <ErrorState onRetry={() => settingsQuery.refetch()} />}
        {settingsQuery.data && (
          <div className="space-y-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              dir="ltr"
              className="h-80 w-full rounded-md border border-border-strong bg-bg-sunken p-3 font-mono text-xs text-fg"
              aria-label={`עריכת ${tab}.yaml`}
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => save.mutate()}
                disabled={save.isPending}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
              >
                <Save size={14} aria-hidden="true" />
                {save.isPending ? "שומר…" : "שמור"}
              </button>
              {saveResult && (
                <span className={cn("text-sm", saveResult.ok ? "text-ok" : "text-danger")}>
                  {saveResult.ok
                    ? "נשמר בהצלחה"
                    : `שגיאות: ${(saveResult.errors ?? []).join("; ") || "שגיאה לא ידועה"}`}
                </span>
              )}
            </div>
          </div>
        )}
      </section>

      <section aria-label={t("settingsJobs.ariaLabel")}>
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">{t("settingsJobs.sectionTitle")}</h2>
        {jobsQuery.isLoading && <LoadingState label={t("settingsJobs.loading")} />}
        {jobsQuery.data && jobsQuery.data.length === 0 && <EmptyState title={t("settingsJobs.empty")} />}
        {jobsQuery.data && jobsQuery.data.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-bg-raised text-xs text-fg-dim">
                <tr>
                  <th className="p-2 text-start">{t("settingsJobs.colId")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colKind")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colSubject")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colState")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colDuration")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colCreated")}</th>
                  <th className="p-2 text-start">{t("settingsJobs.colAction")}</th>
                </tr>
              </thead>
              <tbody>
                {jobsQuery.data.map((j) => (
                  <tr key={j.id} className="border-t border-border hover:bg-bg-sunken">
                    <td className="p-2 font-mono text-xs">{j.id}</td>
                    <td className="p-2">
                      <bdi>{j.kind}</bdi>
                      {typeof j.payload?.mode === "string" && ` · ${j.payload.mode}`}
                    </td>
                    <td className="p-2 text-fg-muted">
                      <bdi>{j.subject_he ?? "—"}</bdi>
                    </td>
                    <td className={cn("p-2 font-medium", JOB_STATE_COLOR[j.state] ?? "text-fg")}>
                      {JOB_STATE_LABEL_KEY[j.state] ? t(JOB_STATE_LABEL_KEY[j.state]) : j.state}
                      {j.state === "failed" && j.error && (
                        <span className="ms-1 text-xs font-normal text-fg-dim" title={j.error}>
                          ({j.error})
                        </span>
                      )}
                    </td>
                    <td className="p-2 font-mono text-xs text-fg-muted">
                      {formatDuration(j.started_at, j.finished_at) ?? "—"}
                    </td>
                    <td className="p-2 font-mono text-xs">{formatDateTime(j.created_at, locale)}</td>
                    <td className="p-2">
                      {(j.state === "queued" || j.state === "running") && (
                        <button
                          type="button"
                          onClick={() => cancelJob.mutate(j.id)}
                          className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-danger hover:bg-bg-sunken"
                        >
                          <Ban size={12} aria-hidden="true" />
                          {t("settingsJobs.cancel")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
