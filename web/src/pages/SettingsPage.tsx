import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Save } from "lucide-react";
import { api } from "@/api";
import { SETTINGS_NAMES, type SettingsName } from "@/types/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";

const TAB_LABEL: Record<SettingsName, string> = {
  config: "config",
  sources: "sources",
  watchlist: "watchlist",
  taxonomy: "taxonomy",
  models: "models",
};

const JOB_STATE_LABEL: Record<string, string> = {
  queued: "בתור",
  running: "רץ",
  done: "הושלם",
  error: "שגיאה",
  cancelled: "בוטל",
};

export function SettingsPage() {
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
      setSaveResult(res);
      if (res.ok) queryClient.invalidateQueries({ queryKey: ["settings", tab] });
    },
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.getJobs(undefined, 50),
    refetchInterval: 8000,
  });

  const cancelJob = useMutation({
    mutationFn: (id: string) => api.postJobCancel(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const runQuick = useMutation({
    mutationFn: (m: "eco" | "full") => api.postRun("daily", m),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-6">
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
                    : `שגיאות: ${saveResult.errors.join("; ")}`}
                </span>
              )}
            </div>
          </div>
        )}
      </section>

      <section aria-label="עבודות (Jobs)">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">עבודות</h2>
        {jobsQuery.isLoading && <LoadingState label="טוען עבודות…" />}
        {jobsQuery.data && jobsQuery.data.length === 0 && <EmptyState title="אין עבודות" />}
        {jobsQuery.data && jobsQuery.data.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[600px] text-sm">
              <thead className="bg-bg-raised text-xs text-fg-dim">
                <tr>
                  <th className="p-2 text-start">מזהה</th>
                  <th className="p-2 text-start">סוג</th>
                  <th className="p-2 text-start">מצב</th>
                  <th className="p-2 text-start">נוצר</th>
                  <th className="p-2 text-start">פעולה</th>
                </tr>
              </thead>
              <tbody>
                {jobsQuery.data.map((j) => (
                  <tr key={j.id} className="border-t border-border hover:bg-bg-sunken">
                    <td className="p-2 font-mono text-xs">{j.id}</td>
                    <td className="p-2">
                      {j.scope} · {j.mode}
                    </td>
                    <td className="p-2">{JOB_STATE_LABEL[j.state] ?? j.state}</td>
                    <td className="p-2 font-mono text-xs">{formatDateTime(j.created_at)}</td>
                    <td className="p-2">
                      {(j.state === "queued" || j.state === "running") && (
                        <button
                          type="button"
                          onClick={() => cancelJob.mutate(j.id)}
                          className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-danger hover:bg-bg-sunken"
                        >
                          <Ban size={12} aria-hidden="true" />
                          בטל
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
