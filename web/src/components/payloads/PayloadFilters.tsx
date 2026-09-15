import { Search } from "lucide-react";
import type { PayloadCategory } from "@/types/api";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

export interface PayloadFiltersState {
  category: PayloadCategory | "";
  vendor: string;
  q: string;
}

const CATEGORY_KEYS: Record<PayloadCategory, TranslationKey> = {
  gimbal: "payloads.categories.gimbal",
  pod: "payloads.categories.pod",
  thermal_camera: "payloads.categories.thermal_camera",
  detector_core: "payloads.categories.detector_core",
  lrf: "payloads.categories.lrf",
  seeker: "payloads.categories.seeker",
  other: "payloads.categories.other",
};

/** W25 (docs/REVIEW_2026-09-06_evening.md): category labels resolved through `t()` so they
 * follow the active locale -- used by `PayloadFilters`, `PayloadTable` and `PayloadDetailDrawer`. */
export function useCategoryLabels(): Record<PayloadCategory, string> {
  const t = useT();
  return {
    gimbal: t(CATEGORY_KEYS.gimbal),
    pod: t(CATEGORY_KEYS.pod),
    thermal_camera: t(CATEGORY_KEYS.thermal_camera),
    detector_core: t(CATEGORY_KEYS.detector_core),
    lrf: t(CATEGORY_KEYS.lrf),
    seeker: t(CATEGORY_KEYS.seeker),
    other: t(CATEGORY_KEYS.other),
  };
}

export function PayloadFilters({
  value,
  onChange,
  vendors,
}: {
  value: PayloadFiltersState;
  onChange: (next: PayloadFiltersState) => void;
  vendors: string[];
}) {
  const t = useT();
  const categoryLabels = useCategoryLabels();
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <select
        value={value.category}
        onChange={(e) => onChange({ ...value, category: e.target.value as PayloadFiltersState["category"] })}
        className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label={t("payloads.filterByCategoryAria")}
      >
        <option value="">{t("payloads.categoryAll")}</option>
        {(Object.keys(categoryLabels) as PayloadCategory[]).map((c) => (
          <option key={c} value={c}>
            {categoryLabels[c]}
          </option>
        ))}
      </select>

      <select
        value={value.vendor}
        onChange={(e) => onChange({ ...value, vendor: e.target.value })}
        className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label={t("payloads.filterByVendorAria")}
      >
        <option value="">{t("payloads.vendorAll")}</option>
        {vendors.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>

      <div className="relative min-w-[10rem] flex-1">
        <Search
          size={14}
          className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-fg-dim"
          style={{ insetInlineStart: "0.5rem" }}
          aria-hidden="true"
        />
        <input
          type="search"
          value={value.q}
          onChange={(e) => onChange({ ...value, q: e.target.value })}
          placeholder={t("payloads.searchPlaceholder")}
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label={t("payloads.searchLabel")}
        />
      </div>

      <a
        href="/api/payloads/export.csv"
        target="_blank"
        rel="noreferrer"
        className="rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg hover:bg-bg-sunken"
      >
        {t("payloads.exportCsv")}
      </a>
    </div>
  );
}
