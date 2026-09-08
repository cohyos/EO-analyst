import { useState, type KeyboardEvent } from "react";
import { Loader2, X } from "lucide-react";
import { PRODUCT_LINE_CATALOG } from "@/lib/productLines";
import { useI18n, useT } from "@/i18n";
import { cn } from "@/lib/cn";
import type { DossierCreateBody } from "@/types/api";

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "סקירה חדשה" -- product name (required),
 * vendor, a chips-style aliases input, a product-line select (`getProductLines`, section 6), and
 * a 1x/2x research-budget select (`budget_multiplier`, section 4). Mirrors `SurveyDialog`'s
 * touched/inline-error validation pattern (`web/src/components/patents/SurveyDialog.tsx`), kept
 * as a plain inline form rather than a modal since the list page has room for it.
 */
export function NewDossierForm({
  onSubmit,
  onCancel,
  submitting,
}: {
  onSubmit: (body: DossierCreateBody) => void;
  onCancel: () => void;
  submitting: boolean;
}) {
  const t = useT();
  const { locale } = useI18n();
  const [productName, setProductName] = useState("");
  const [vendor, setVendor] = useState("");
  const [aliasInput, setAliasInput] = useState("");
  const [aliases, setAliases] = useState<string[]>([]);
  const [productLine, setProductLine] = useState("");
  const [budgetMultiplier, setBudgetMultiplier] = useState(1);
  const [touched, setTouched] = useState(false);

  const trimmedName = productName.trim();
  const isValid = trimmedName.length > 0;
  const showError = touched && !isValid;

  function addAlias(raw: string) {
    const value = raw.trim();
    if (!value) return;
    setAliases((cur) => (cur.includes(value) ? cur : [...cur, value]));
    setAliasInput("");
  }

  function handleAliasKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addAlias(aliasInput);
    } else if (e.key === "Backspace" && aliasInput === "" && aliases.length > 0) {
      setAliases((cur) => cur.slice(0, -1));
    }
  }

  function removeAlias(alias: string) {
    setAliases((cur) => cur.filter((a) => a !== alias));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!isValid) return;
    const finalAliases = aliasInput.trim() && !aliases.includes(aliasInput.trim())
      ? [...aliases, aliasInput.trim()]
      : aliases;
    onSubmit({
      product_name: trimmedName,
      vendor: vendor.trim() || null,
      aliases: finalAliases,
      product_line: productLine || null,
      budget_multiplier: budgetMultiplier,
    });
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label={t("dossiers.form.title")}
      className="space-y-3 rounded-lg border border-border bg-bg-raised p-4"
    >
      <h3 className="text-sm font-semibold text-fg">{t("dossiers.form.title")}</h3>

      <div>
        <label htmlFor="dossier-product-name" className="mb-1 block text-xs text-fg-muted">
          {t("dossiers.form.productNameLabel")} *
        </label>
        <input
          id="dossier-product-name"
          dir="auto"
          value={productName}
          onChange={(e) => setProductName(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder={t("dossiers.form.productNamePlaceholder")}
          aria-required="true"
          aria-invalid={showError || undefined}
          aria-describedby={showError ? "dossier-product-name-error" : undefined}
          className={cn(
            "w-full rounded-md border bg-bg-sunken p-2 text-sm outline-none focus:border-accent",
            showError ? "border-danger" : "border-border",
          )}
        />
        {showError && (
          <p id="dossier-product-name-error" role="alert" className="mt-1 text-xs text-danger">
            {t("dossiers.form.productNameRequired")}
          </p>
        )}
      </div>

      <div>
        <label htmlFor="dossier-vendor" className="mb-1 block text-xs text-fg-muted">
          {t("dossiers.form.vendorLabel")}
        </label>
        <input
          id="dossier-vendor"
          dir="auto"
          value={vendor}
          onChange={(e) => setVendor(e.target.value)}
          placeholder={t("dossiers.form.vendorPlaceholder")}
          className="w-full rounded-md border border-border bg-bg-sunken p-2 text-sm outline-none focus:border-accent"
        />
      </div>

      <div>
        <label htmlFor="dossier-aliases" className="mb-1 block text-xs text-fg-muted">
          {t("dossiers.form.aliasesLabel")}
        </label>
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-bg-sunken p-1.5">
          {aliases.map((alias) => (
            <span
              key={alias}
              className="flex items-center gap-1 rounded-full bg-accent-muted px-2 py-0.5 text-xs text-accent-fg"
            >
              <bdi>{alias}</bdi>
              <button
                type="button"
                onClick={() => removeAlias(alias)}
                aria-label={t("dossiers.form.aliasesRemoveAria", { alias })}
                className="hover:opacity-70"
              >
                <X size={11} aria-hidden="true" />
              </button>
            </span>
          ))}
          <input
            id="dossier-aliases"
            dir="auto"
            value={aliasInput}
            onChange={(e) => setAliasInput(e.target.value)}
            onKeyDown={handleAliasKeyDown}
            onBlur={() => addAlias(aliasInput)}
            placeholder={aliases.length === 0 ? t("dossiers.form.aliasesPlaceholder") : undefined}
            className="min-w-32 flex-1 bg-transparent text-sm outline-none"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="dossier-product-line" className="mb-1 block text-xs text-fg-muted">
            {t("dossiers.form.productLineLabel")}
          </label>
          <select
            id="dossier-product-line"
            value={productLine}
            onChange={(e) => setProductLine(e.target.value)}
            className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
          >
            <option value="">{t("dossiers.form.productLineNone")}</option>
            {PRODUCT_LINE_CATALOG.map((p) => (
              <option key={p.id} value={p.id}>
                {locale === "he" ? p.nameHe : p.nameEn}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="dossier-budget" className="mb-1 block text-xs text-fg-muted">
            {t("dossiers.form.budgetLabel")}
          </label>
          <select
            id="dossier-budget"
            value={budgetMultiplier}
            onChange={(e) => setBudgetMultiplier(Number(e.target.value))}
            className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
          >
            <option value={1}>{t("dossiers.form.budget1x")}</option>
            <option value={2}>{t("dossiers.form.budget2x")}</option>
          </select>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-fg-muted hover:bg-bg-sunken"
        >
          {t("dossiers.form.cancel")}
        </button>
        <button
          type="submit"
          disabled={submitting}
          data-testid="dossier-form-submit"
          className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
        >
          {submitting && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
          {t("dossiers.form.submit")}
        </button>
      </div>
    </form>
  );
}
