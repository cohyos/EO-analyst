import { useI18n } from "@/i18n";
import type { BdTerritoryOption } from "@/types/api";
import { countryOption } from "@/lib/countries";

/**
 * A11 territory selector: flags + human names (reusing `@/lib/countries`'s catalog, the same one
 * the Feed's country filter uses) plus item/tender/forecast activity counts from `GET
 * /api/bd/territories`, so the analyst can see at a glance which territories have material worth
 * a report before picking one.
 */
export function TerritorySelector({
  territories,
  selected,
  onSelect,
  loading,
}: {
  territories: BdTerritoryOption[];
  selected: string;
  onSelect: (code: string) => void;
  loading?: boolean;
}) {
  const { t, locale } = useI18n();
  return (
    <div>
      <label htmlFor="bd-territory" className="mb-1 block text-xs text-fg-dim">
        {t("bd.territoryLabel")}
      </label>
      <select
        id="bd-territory"
        value={selected}
        onChange={(e) => onSelect(e.target.value)}
        disabled={loading}
        className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
      >
        <option value="" disabled>
          {loading ? t("common.loading") : t("bd.territoryLabel")}
        </option>
        {territories.map((opt) => {
          const country = countryOption(opt.territory);
          const label = locale === "he" ? country.nameHe : country.nameEn;
          const total = opt.items + opt.tenders + opt.forecasts;
          return (
            <option key={opt.territory} value={opt.territory}>
              {country.flag} {label} ({opt.territory}) — {total}
            </option>
          );
        })}
      </select>
    </div>
  );
}
