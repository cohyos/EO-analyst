import { useQuery } from "@tanstack/react-query";
import { Cloud, Cpu } from "lucide-react";
import { api } from "@/api";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const POWER_KEY: Record<string, TranslationKey | undefined> = {
  low: "llm.power.low",
  medium: "llm.power.medium",
  high: "llm.power.high",
};

/** Splits a picker value into its parts: `null`/`""` -> system default; `"ollama"` -> local (no
 * model/power); `"<id>:<model>"` or `"<id>:<model>@<power>"` -> a cloud/API provider's choice
 * (U8-ג's "<model>[@<power>]" wire format, `ollama_client._dispatch_explicit_provider`). */
function parsePickerValue(value: string | null): {
  providerId: string | null;
  model: string | null;
  power: string | null;
} {
  if (!value) return { providerId: null, model: null, power: null };
  const colonIdx = value.indexOf(":");
  if (colonIdx === -1) return { providerId: value, model: null, power: null };
  const providerId = value.slice(0, colonIdx);
  let rest = value.slice(colonIdx + 1);
  let power: string | null = null;
  const atIdx = rest.lastIndexOf("@");
  if (atIdx !== -1) {
    power = rest.slice(atIdx + 1) || null;
    rest = rest.slice(0, atIdx);
  }
  return { providerId, model: rest || null, power };
}

/**
 * U8 (docs/adr/005-cloud-llm-cli.md + "Revision 2026-09-06"): local Ollama vs. cloud CLI
 * (agy/claude/codex) vs. direct-API (anthropic/gemini/openai) picker for the chat composer.
 * Grouped one `<optgroup>` per PROVIDER (not just per kind) so "Gemini (Antigravity CLI)",
 * "Claude (Claude Code CLI)", "Anthropic (API)" etc. are visually separate instead of one long
 * flat "ענן" list. A provider with `power_levels` gets a second, dependent `<select>` for the
 * effort/thinking level (U8-ג) — hidden entirely for providers/local that have none. An empty
 * value means "ברירת המחדל של המערכת" / "לפי ההגדרות" (server's `llm_providers.
 * interactive_default`), so the picker never has to guess or duplicate that default. Cloud
 * options are disabled (not hidden) when unavailable or when the admin kill-switch
 * (`allow_cloud`) is off, so the reason is visible instead of the option vanishing. The caller
 * (`useAskChat`) persists whatever full value string this emits to `localStorage`, so the last
 * choice (including any power suffix) is remembered across sessions.
 */
export function ModelPicker({
  value,
  onChange,
  compact,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  compact?: boolean;
}) {
  const t = useT();
  const { data } = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => api.getLlmProviders(),
    staleTime: 60_000,
  });

  const providers = data?.providers ?? [];
  const local = providers.filter((p) => p.kind === "local");
  const cloudAndApi = providers.filter((p) => p.kind === "cloud" || p.kind === "api");
  const isCloudSelected = !!value && value !== "ollama" && !value.startsWith("ollama:");

  const { providerId, model, power } = parsePickerValue(value);
  const selectedProvider = providerId ? providers.find((p) => p.id === providerId) : undefined;
  const powerLevels = selectedProvider?.power_levels ?? [];
  const selectValue = providerId && model ? `${providerId}:${model}` : (providerId ?? "");

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", compact && "text-xs")}>
      <label className="sr-only" htmlFor="ask-model-picker">
        בחירת מודל (מקומי או ענן)
      </label>
      <select
        id="ask-model-picker"
        value={selectValue}
        onChange={(e) => onChange(e.target.value || null)}
        title={
          isCloudSelected
            ? "הטקסט של השיחה יישלח לשירות ענן חיצוני"
            : "מודל מקומי — אינו יוצא מהמחשב"
        }
        className={cn(
          "max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-fg",
          compact ? "text-xs" : "text-sm",
        )}
      >
        <option value="">ברירת המחדל של המערכת (לפי ההגדרות)</option>
        {local.length > 0 && (
          <optgroup label="מקומי">
            {local.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.available}>
                {p.label}
                {!p.available ? " (לא זמין)" : ""}
              </option>
            ))}
          </optgroup>
        )}
        {data &&
          data.allow_cloud &&
          cloudAndApi.map((p) => (
            <optgroup key={p.id} label={p.label}>
              {(p.models.length > 0 ? p.models : ["default"]).map((m) => (
                <option key={`${p.id}:${m}`} value={`${p.id}:${m}`} disabled={!p.available}>
                  {m}
                  {!p.available
                    ? ` (${p.kind === "api" ? "לא מוגדר מפתח" : "לא מותקן"})`
                    : ""}
                </option>
              ))}
            </optgroup>
          ))}
      </select>

      {/* U8-ג: effort/thinking level — only shown when the selected provider offers one. */}
      {providerId && model && powerLevels.length > 0 && (
        <select
          aria-label={t("llm.powerLabel")}
          value={power ?? ""}
          onChange={(e) => onChange(`${providerId}:${model}${e.target.value ? `@${e.target.value}` : ""}`)}
          className={cn(
            "max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-fg",
            compact ? "text-xs" : "text-sm",
          )}
        >
          <option value="">{t("llm.chains.powerDefault")}</option>
          {powerLevels.map((lvl) => {
            const key = POWER_KEY[lvl];
            return (
              <option key={lvl} value={lvl}>
                {key ? t(key) : lvl}
              </option>
            );
          })}
        </select>
      )}

      {isCloudSelected ? (
        <span
          className="flex items-center gap-1 text-danger"
          title="הטקסט של השיחה יישלח לשירות ענן חיצוני"
        >
          <Cloud size={13} aria-hidden="true" />
          {!compact && <span className="text-xs">ענן</span>}
        </span>
      ) : (
        <span className="flex items-center gap-1 text-fg-dim" title="מודל מקומי — אינו יוצא מהמחשב">
          <Cpu size={13} aria-hidden="true" />
          {!compact && <span className="text-xs">מקומי</span>}
        </span>
      )}
    </div>
  );
}
