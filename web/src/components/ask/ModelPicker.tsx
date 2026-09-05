import { useQuery } from "@tanstack/react-query";
import { Cloud, Cpu } from "lucide-react";
import { api } from "@/api";
import { cn } from "@/lib/cn";

/**
 * U8 (docs/adr/005-cloud-llm-cli.md): local Ollama vs. cloud CLI (agy/claude/codex) picker for
 * the chat composer. Grouped "מקומי" / "ענן" — an empty value means "ברירת המחדל של המערכת"
 * (server's `llm_providers.interactive_default`), so the picker never has to guess or duplicate
 * that default. Cloud options are disabled (not hidden) when unavailable or when the admin
 * kill-switch (`allow_cloud`) is off, so the reason is visible instead of the option vanishing.
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
  const { data } = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => api.getLlmProviders(),
    staleTime: 60_000,
  });

  const providers = data?.providers ?? [];
  const local = providers.filter((p) => p.kind === "local");
  const cloud = providers.filter((p) => p.kind === "cloud");
  // U8-ו (Revision 2026-09-06): direct-API providers (anthropic/gemini/openai) — same picker
  // shape as a cloud CLI (id:model), only ever offered when the key is configured server-side.
  const apiProviders = providers.filter((p) => p.kind === "api");
  const isCloudSelected = !!value && value !== "ollama" && !value.startsWith("ollama:");

  return (
    <div className={cn("flex items-center gap-1.5", compact && "text-xs")}>
      <label className="sr-only" htmlFor="ask-model-picker">
        בחירת מודל (מקומי או ענן)
      </label>
      <select
        id="ask-model-picker"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        title={
          isCloudSelected
            ? "הטקסט של השיחה יישלח לשירות ענן חיצוני"
            : "מודל מקומי — אינו יוצא מהמחשב"
        }
        className={cn(
          "rounded-md border border-border-strong bg-bg px-2 py-1.5 text-fg",
          compact ? "text-xs" : "text-sm",
        )}
      >
        <option value="">ברירת המחדל של המערכת</option>
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
        {data && data.allow_cloud && cloud.length > 0 && (
          <optgroup label="ענן (CLI)">
            {cloud.flatMap((p) =>
              p.models.length > 0
                ? p.models.map((m) => (
                    <option key={`${p.id}:${m}`} value={`${p.id}:${m}`} disabled={!p.available}>
                      {p.label} — {m}
                      {!p.available ? " (לא מותקן)" : ""}
                    </option>
                  ))
                : [
                    <option key={p.id} value={p.id} disabled={!p.available}>
                      {p.label}
                      {!p.available ? " (לא מותקן)" : ""}
                    </option>,
                  ],
            )}
          </optgroup>
        )}
        {data && data.allow_cloud && apiProviders.length > 0 && (
          <optgroup label="ענן (API)">
            {apiProviders.flatMap((p) =>
              p.models.map((m) => (
                <option key={`${p.id}:${m}`} value={`${p.id}:${m}`} disabled={!p.available}>
                  {p.label} — {m}
                  {!p.available ? " (לא מוגדר מפתח)" : ""}
                </option>
              )),
            )}
          </optgroup>
        )}
      </select>
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
