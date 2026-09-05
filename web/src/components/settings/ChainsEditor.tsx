import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Copy, GripVertical, Plus, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";
import type { LlmChainEntry, LlmProviderInfo } from "@/types/api";

/**
 * U8-ה/ג (docs/adr/005-cloud-llm-cli.md, "Revision 2026-09-06"): per-role fallback-chain editor
 * for the Settings "מודלים" card. Until this component existed, `llm_providers.chains` could only
 * be hand-edited in config.yaml (or through the raw YAML tab below) -- this gives every role
 * (resident/investigator/light/report) its own ordered list of {provider, model, power} steps,
 * add/remove/reorder (buttons + drag), a fixed non-removable "מקומי (Ollama)" terminal row (the
 * server always appends one on save if missing -- `services._with_terminal_ollama` -- so the UI
 * just documents that truth rather than needing its own copy of the entry), and a "העתק לכל
 * התפקידים" convenience action.
 *
 * Saving is owned by the parent (`SettingsPage.tsx`'s `putLlmSettings` mutation) -- this component
 * only tracks a local draft (so typing/reordering is instant, no per-keystroke network round trip)
 * and a dirty flag computed against the last-known-saved chains, resyncing from the server only
 * while there is nothing unsaved locally (so a background refetch, e.g. the calls-summary card's
 * 30s poll invalidating sibling queries, can never clobber an in-progress edit).
 */

const ROLES = ["resident", "investigator", "light", "report"] as const;
type Role = (typeof ROLES)[number];

const ROLE_KEY: Record<Role, TranslationKey> = {
  resident: "llm.chains.role.resident",
  investigator: "llm.chains.role.investigator",
  light: "llm.chains.role.light",
  report: "llm.chains.role.report",
};

const POWER_KEY: Record<string, TranslationKey | undefined> = {
  low: "llm.power.low",
  medium: "llm.power.medium",
  high: "llm.power.high",
};

function emptyChains(): Record<Role, LlmChainEntry[]> {
  return { resident: [], investigator: [], light: [], report: [] };
}

/** The server always persists a trailing `{provider: "ollama"}` terminal step for every role
 * it's given (`services._with_terminal_ollama` -- even an intentionally empty chain round-trips
 * as `[{provider: "ollama"}]`, matching what `effective_chain` would resolve to anyway). The UI
 * renders its OWN fixed, decorative "מקומי (Ollama)" terminal row below the editable list, so a
 * trailing ollama entry coming back from the server must be treated as that same implicit step,
 * not a second, editable one -- otherwise every reload after a save shows a phantom duplicate
 * "ollama" row at the end of the editable steps. */
function stripImplicitTerminal(chain: LlmChainEntry[]): LlmChainEntry[] {
  if (chain.length > 0 && chain[chain.length - 1].provider === "ollama") return chain.slice(0, -1);
  return chain;
}

function cloneChains(chains: Record<string, LlmChainEntry[]>): Record<Role, LlmChainEntry[]> {
  const out = emptyChains();
  for (const role of ROLES) out[role] = stripImplicitTerminal(chains[role] ?? []).map((e) => ({ ...e }));
  return out;
}

function chainsEqual(a: Record<Role, LlmChainEntry[]>, b: Record<Role, LlmChainEntry[]>): boolean {
  return ROLES.every((role) => {
    const ca = a[role];
    const cb = b[role];
    if (ca.length !== cb.length) return false;
    return ca.every(
      (e, i) =>
        e.provider === cb[i].provider &&
        (e.model ?? "") === (cb[i].model ?? "") &&
        (e.power ?? "") === (cb[i].power ?? ""),
    );
  });
}

export function ChainsEditor({
  chains,
  providers,
  allowCloud,
  onSave,
  saving,
  saveResult,
}: {
  chains: Record<string, LlmChainEntry[]>;
  providers: LlmProviderInfo[];
  allowCloud: boolean;
  onSave: (chains: Record<string, LlmChainEntry[]>) => void;
  saving: boolean;
  saveResult: { ok: boolean; errors: string[] } | null;
}) {
  const t = useT();
  const [activeRole, setActiveRole] = useState<Role>("resident");
  const [draft, setDraft] = useState<Record<Role, LlmChainEntry[]>>(() => cloneChains(chains));
  const [savedBaseline, setSavedBaseline] = useState<Record<Role, LlmChainEntry[]>>(() => cloneChains(chains));
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [toastErrors, setToastErrors] = useState<string[] | null>(null);
  const lastSaveResultRef = useRef<typeof saveResult>(null);

  const dirty = !chainsEqual(draft, savedBaseline);

  // Resync from the server only while nothing is unsaved locally.
  useEffect(() => {
    setDraft((prevDraft) => {
      if (!chainsEqual(prevDraft, savedBaseline)) return prevDraft;
      const next = cloneChains(chains);
      setSavedBaseline(next);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chains]);

  useEffect(() => {
    if (saveResult === lastSaveResultRef.current) return;
    lastSaveResultRef.current = saveResult;
    if (!saveResult) return;
    if (saveResult.ok) {
      setSavedBaseline(cloneChains(draft));
      setToastErrors(null);
    } else {
      setToastErrors(saveResult.errors.length > 0 ? saveResult.errors : [t("llm.chains.saveErrorGeneric")]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveResult]);

  useEffect(() => {
    if (!toastErrors) return;
    const timer = setTimeout(() => setToastErrors(null), 8000);
    return () => clearTimeout(timer);
  }, [toastErrors]);

  const ollamaLabel = providers.find((p) => p.id === "ollama")?.label ?? "Ollama";
  const stepProviders = useMemo(() => providers.filter((p) => p.kind === "cloud" || p.kind === "api"), [providers]);
  const providerById = useMemo(() => new Map(stepProviders.map((p) => [p.id, p])), [stepProviders]);
  const canAddStep = stepProviders.length > 0;

  function optionsFor(entryProvider: string): LlmProviderInfo[] {
    if (providerById.has(entryProvider)) return stepProviders;
    // The role's current chain references a provider not currently offered (e.g. `allow_cloud`
    // was toggled off after this chain was saved) -- keep it selectable instead of silently
    // switching the row to whatever option happens to be first.
    return [...stepProviders, { id: entryProvider, label: entryProvider, kind: "cloud", available: false, models: [] }];
  }

  function updateRole(role: Role, next: LlmChainEntry[]) {
    setDraft((prev) => ({ ...prev, [role]: next }));
  }

  function addStep(role: Role) {
    const first = stepProviders[0];
    if (!first) return;
    updateRole(role, [...draft[role], { provider: first.id, model: first.models[0] }]);
  }

  function removeStep(role: Role, index: number) {
    updateRole(
      role,
      draft[role].filter((_, i) => i !== index),
    );
  }

  function moveStep(role: Role, index: number, dir: -1 | 1) {
    const chain = draft[role];
    const target = index + dir;
    if (target < 0 || target >= chain.length) return;
    const next = chain.slice();
    [next[index], next[target]] = [next[target], next[index]];
    updateRole(role, next);
  }

  function patchStep(role: Role, index: number, patch: Partial<LlmChainEntry>) {
    updateRole(
      role,
      draft[role].map((e, i) => (i === index ? { ...e, ...patch } : e)),
    );
  }

  function handleDrop(role: Role, index: number) {
    if (dragIndex === null || dragIndex === index) {
      setDragIndex(null);
      return;
    }
    const chain = draft[role].slice();
    const [moved] = chain.splice(dragIndex, 1);
    chain.splice(index, 0, moved);
    updateRole(role, chain);
    setDragIndex(null);
  }

  function copyToAllRoles(role: Role) {
    const source = draft[role].map((e) => ({ ...e }));
    setDraft((prev) => {
      const next = { ...prev };
      for (const r of ROLES) {
        if (r !== role) next[r] = source.map((e) => ({ ...e }));
      }
      return next;
    });
  }

  const activeChain = draft[activeRole];

  return (
    <div
      role="region"
      aria-label={t("llm.chains.title")}
      className="space-y-3 rounded-md border border-border-strong bg-bg p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-fg">{t("llm.chains.title")}</h3>
        {dirty && (
          <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
            {t("llm.chains.unsaved")}
          </span>
        )}
      </div>

      {!allowCloud || !canAddStep ? (
        <p className="text-xs text-fg-dim">{t("llm.chains.cloudDisabledHint")}</p>
      ) : null}

      <div className="flex flex-wrap gap-1 border-b border-border" role="tablist" aria-label={t("llm.chains.title")}>
        {ROLES.map((role) => (
          <button
            key={role}
            type="button"
            role="tab"
            aria-selected={activeRole === role}
            onClick={() => setActiveRole(role)}
            className={cn(
              "rounded-t-md px-2.5 py-1 text-xs font-medium",
              activeRole === role
                ? "border-x border-t border-border bg-bg-raised text-fg"
                : "text-fg-dim hover:text-fg",
            )}
          >
            {t(ROLE_KEY[role])}
            {draft[role].length > 0 && (
              <span className="ms-1 text-fg-dim">({draft[role].length})</span>
            )}
          </button>
        ))}
      </div>

      <ul className="space-y-1.5" aria-label={t(ROLE_KEY[activeRole])}>
        {activeChain.map((entry, index) => {
          const provider = providerById.get(entry.provider);
          const powerLevels = provider?.power_levels ?? [];
          const rowLabel = t("llm.chains.stepLabel", { index: index + 1 });
          return (
            <li
              key={index}
              draggable
              onDragStart={() => setDragIndex(index)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(activeRole, index)}
              aria-label={rowLabel}
              className="flex flex-wrap items-center gap-1.5 rounded-md border border-border-strong bg-bg-raised p-1.5"
            >
              <span className="shrink-0" title={t("llm.chains.dragHandle")}>
                <GripVertical size={14} aria-hidden="true" className="cursor-grab text-fg-dim" />
              </span>
              <span className="shrink-0 text-xs text-fg-dim">{index + 1}.</span>

              <select
                aria-label={t("llm.chains.providerLabel")}
                value={entry.provider}
                onChange={(e) => {
                  const next = providerById.get(e.target.value);
                  patchStep(activeRole, index, {
                    provider: e.target.value,
                    model: next?.models[0],
                    power: undefined,
                  });
                }}
                className="rounded-md border border-border-strong bg-bg px-2 py-1 text-xs text-fg"
              >
                {optionsFor(entry.provider).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                    {!p.available ? ` (${p.kind === "api" ? t("llm.keyNotConfigured") : t("llm.chains.notInstalled")})` : ""}
                  </option>
                ))}
              </select>

              <select
                aria-label={t("llm.chains.modelLabel")}
                value={entry.model ?? ""}
                onChange={(e) => patchStep(activeRole, index, { model: e.target.value })}
                className="rounded-md border border-border-strong bg-bg px-2 py-1 text-xs text-fg"
              >
                {entry.model && !(provider?.models ?? []).includes(entry.model) && (
                  <option value={entry.model}>{entry.model}</option>
                )}
                {(provider?.models ?? []).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>

              {powerLevels.length > 0 && (
                <select
                  aria-label={t("llm.powerLabel")}
                  value={entry.power ?? ""}
                  onChange={(e) => patchStep(activeRole, index, { power: e.target.value || undefined })}
                  className="rounded-md border border-border-strong bg-bg px-2 py-1 text-xs text-fg"
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

              <div className="ms-auto flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => moveStep(activeRole, index, -1)}
                  disabled={index === 0}
                  aria-label={t("llm.chains.moveUp")}
                  className="rounded p-1 text-fg-dim hover:bg-bg-sunken disabled:opacity-30"
                >
                  <ArrowUp size={13} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => moveStep(activeRole, index, 1)}
                  disabled={index === activeChain.length - 1}
                  aria-label={t("llm.chains.moveDown")}
                  className="rounded p-1 text-fg-dim hover:bg-bg-sunken disabled:opacity-30"
                >
                  <ArrowDown size={13} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => removeStep(activeRole, index)}
                  aria-label={t("llm.chains.removeStep")}
                  className="rounded p-1 text-danger hover:bg-bg-sunken"
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </div>
            </li>
          );
        })}

        <li
          aria-label={t("llm.chains.terminalStepLabel")}
          className="flex items-center gap-1.5 rounded-md border border-dashed border-border-strong bg-bg-sunken/60 p-1.5 text-xs text-fg-dim"
        >
          <span className="shrink-0 opacity-60">{activeChain.length + 1}.</span>
          <bdi>{ollamaLabel}</bdi>
          <span>({t("llm.chains.terminalHint")})</span>
        </li>
      </ul>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => addStep(activeRole)}
          disabled={!canAddStep}
          className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg hover:bg-bg-sunken disabled:opacity-40"
        >
          <Plus size={12} aria-hidden="true" />
          {t("llm.chains.addStep")}
        </button>
        <button
          type="button"
          onClick={() => copyToAllRoles(activeRole)}
          className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg hover:bg-bg-sunken"
        >
          <Copy size={12} aria-hidden="true" />
          {t("llm.chains.copyToAll")}
        </button>
        <button
          type="button"
          onClick={() => onSave(draft)}
          disabled={saving || !dirty}
          className="ms-auto rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
        >
          {saving ? t("llm.chains.saving") : t("llm.chains.save")}
        </button>
      </div>

      {!dirty && saveResult?.ok && <p className="text-xs text-ok">{t("llm.chains.savedOk")}</p>}

      {toastErrors && (
        <div
          role="alert"
          aria-live="assertive"
          className="fixed bottom-4 start-4 z-50 flex max-w-sm items-start gap-3 rounded-lg border border-danger/40 bg-bg-raised px-4 py-3 text-sm text-danger shadow-panel"
        >
          <span className="flex-1">{toastErrors.join("; ")}</span>
          <button
            type="button"
            onClick={() => setToastErrors(null)}
            aria-label={t("common.close")}
            className="rounded p-0.5 text-fg-dim hover:bg-bg-sunken"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
