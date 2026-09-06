// A8 (docs/adr/006-mcp-sources.md): Settings card for MCP (Model Context Protocol) tool
// sources — read-only, allow-listed, DATA-framed exactly like a fetched web page. Lists every
// configured server (config/mcp.yaml) with its transport, enabled/disabled state, tool count,
// key-configured indicator, and a "בדוק חיבור" (check connection) button per server, plus a
// 24h MCP call-accounting summary. Actual enable/disable of a server happens in config/mcp.yaml
// (editable via the "config" tab below, or the raw file directly) — this card is status +
// diagnostics, matching the endpoints GET/POST /api/mcp/* actually expose (no separate
// write-toggle endpoint is defined for A8).
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plug, RefreshCw, XCircle } from "lucide-react";
import { api } from "@/api";
import type { McpServerInfo } from "@/types/api";
import { LoadingState } from "@/components/states";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

type StatusTone = "dim" | "warn" | "ok";

// Q5-12 (docs/qa/findings_Q5_r2.md): the card used to show every server as "פעיל" (active) purely
// off `server.enabled` (the yaml flag), regardless of the global MCP switch or whether a required
// key was actually set -- so a server read "active" while nothing could actually reach it. One
// status now folds all three inputs, in priority order: the global switch beats everything (if
// it's off, nothing is reachable no matter what each server's own flag/key says); then the
// server's own enabled flag; then whether a required key is present; "connected"/"error" only
// replace the resting "configured" label once an actual ping has run (or the last one on record).
function serverStatus(
  mcpEnabled: boolean,
  server: McpServerInfo,
  pingResult: { ok: boolean; error: string | null } | null,
): { labelKey: TranslationKey; tone: StatusTone } {
  if (!mcpEnabled) return { labelKey: "mcp.status.globalOff", tone: "dim" };
  if (!server.enabled) return { labelKey: "mcp.status.serverOff", tone: "dim" };
  if (server.key_configured === false) return { labelKey: "mcp.status.keyMissing", tone: "warn" };
  const ok = pingResult?.ok ?? server.ok;
  if (ok === true) return { labelKey: "mcp.status.connected", tone: "ok" };
  if (ok === false) return { labelKey: "mcp.status.error", tone: "warn" };
  return { labelKey: "mcp.status.configured", tone: "ok" };
}

const STATUS_CHIP_CLASS: Record<StatusTone, string> = {
  dim: "bg-bg-sunken text-fg-dim",
  warn: "bg-danger/15 text-danger",
  ok: "bg-ok/15 text-ok",
};

function ServerRow({ server, mcpEnabled }: { server: McpServerInfo; mcpEnabled: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [pingResult, setPingResult] = useState<{ ok: boolean; error: string | null; tool_count: number } | null>(
    null,
  );

  const ping = useMutation({
    mutationFn: () => api.postMcpServerPing(server.id),
    onSuccess: (res) => {
      setPingResult({ ok: res.ok, error: res.error, tool_count: res.tool_count });
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    },
    onError: (err: unknown) => {
      setPingResult({ ok: false, error: err instanceof Error ? err.message : String(err), tool_count: 0 });
    },
  });

  const toolCount = pingResult?.tool_count ?? server.tool_count;
  const status = serverStatus(mcpEnabled, server, pingResult);

  return (
    <li className="rounded-md border border-border-strong bg-bg p-2">
      <div className="flex flex-wrap items-center gap-2">
        <Plug size={14} aria-hidden="true" className="text-fg-dim" />
        <bdi className="text-sm font-medium text-fg">{server.label}</bdi>
        <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", STATUS_CHIP_CLASS[status.tone])}>
          {t(status.labelKey)}
        </span>
        <span className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-dim">
          {server.transport === "http" ? t("mcp.transport.http") : t("mcp.transport.stdio")}
        </span>
        {server.inherit_cli_only && (
          <span className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-dim">
            {t("mcp.inheritCliOnly")}
          </span>
        )}
        {/* Q5-12: which env var(s) a key comes from -- never the value itself. Configured/missing
            is already conveyed by the status chip above, so this is just the variable name(s). */}
        {server.key_env && server.key_env.length > 0 && (
          <span className="text-xs text-fg-dim">{server.key_env.join(", ")}</span>
        )}
        {typeof toolCount === "number" && (
          <span className="text-xs text-fg-dim">{t("mcp.toolCount", { count: toolCount })}</span>
        )}
        <button
          type="button"
          onClick={() => ping.mutate()}
          disabled={ping.isPending || server.inherit_cli_only}
          className="ms-auto flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken disabled:opacity-50"
        >
          <RefreshCw size={12} aria-hidden="true" className={ping.isPending ? "animate-spin" : ""} />
          {ping.isPending ? t("mcp.pinging") : t("mcp.ping")}
        </button>
      </div>
      {(pingResult || server.error) && (
        <p className="mt-1 flex items-center gap-1 text-xs">
          {(pingResult?.ok ?? server.ok) ? (
            <>
              <CheckCircle2 size={12} aria-hidden="true" className="text-ok" />
              <span className="text-ok">{t("mcp.pingOk")}</span>
            </>
          ) : (
            <>
              <XCircle size={12} aria-hidden="true" className="text-danger" />
              <span className="text-danger">
                {t("mcp.pingFailed")}
                {pingResult?.error || server.error ? `: ${pingResult?.error ?? server.error}` : ""}
              </span>
            </>
          )}
        </p>
      )}
    </li>
  );
}

export function MCPCard() {
  const t = useT();
  const serversQuery = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => api.getMcpServers(),
  });
  const callsQuery = useQuery({
    queryKey: ["mcp-calls"],
    queryFn: () => api.getMcpCalls("24h"),
    refetchInterval: 30_000,
  });

  return (
    <section aria-label="MCP" className="rounded-lg border border-border bg-bg-raised p-3">
      <h2 className="mb-2 text-sm font-semibold text-fg-dim">{t("mcp.title")}</h2>
      {serversQuery.isLoading && <LoadingState />}
      {serversQuery.data && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border-strong bg-bg p-2">
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                serversQuery.data.mcp_enabled ? "bg-ok/15 text-ok" : "bg-bg-sunken text-fg-dim",
              )}
            >
              {serversQuery.data.mcp_enabled ? t("mcp.globalEnabled") : t("mcp.globalDisabled")}
            </span>
            <p className="w-full text-xs text-fg-dim">{t("mcp.globalHint")}</p>
          </div>

          <ul className="space-y-2">
            {serversQuery.data.servers.map((s) => (
              <ServerRow key={s.id} server={s} mcpEnabled={serversQuery.data.mcp_enabled} />
            ))}
          </ul>

          {callsQuery.data && (
            <p className="text-xs text-fg-dim">
              {callsQuery.data.totals.calls > 0
                ? t("mcp.callsSummary", {
                    calls: callsQuery.data.totals.calls,
                    failures: callsQuery.data.totals.failures,
                  })
                : t("mcp.callsSummaryEmpty")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
