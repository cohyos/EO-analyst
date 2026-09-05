import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/api";
import type { CountryGroup, TriageLevel } from "@/types/api";
import { countryOption } from "@/lib/countries";
import { useI18n, useT } from "@/i18n";
import { cn } from "@/lib/cn";

/**
 * U7c: compact "מפת מדינות" panel — countries sorted by item count with a
 * red/orange/yellow level breakdown; clicking a row toggles that country
 * into the feed's country filter. Reflects the feed's *current* level/
 * domain/date filters (via `GET /api/items/by-country`), not the whole DB.
 */
export function CountryMapPanel({
  levels,
  domain,
  selected,
  onToggle,
  onClose,
}: {
  levels: TriageLevel[];
  domain: string;
  selected: string[];
  onToggle: (code: string) => void;
  onClose: () => void;
}) {
  const t = useT();
  const { locale } = useI18n();
  const apiLevels = levels.filter((l): l is Exclude<TriageLevel, "unclassified"> => l !== "unclassified");

  const { data, isLoading } = useQuery({
    queryKey: ["items-by-country", apiLevels, domain],
    queryFn: () => api.getItemsByCountry({ level: apiLevels.length ? apiLevels : undefined, domain: domain || undefined }),
  });

  const countries: CountryGroup[] = data?.countries ?? [];

  return (
    <div
      data-testid="country-map-panel"
      className="border-b border-border bg-bg-raised px-3 py-2"
    >
      <div className="mb-1.5 flex items-center justify-between">
        <h2 className="text-xs font-semibold text-fg-dim">{t("feed.countryPanelTitle")}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("common.close")}
          className="rounded p-1 text-fg-muted hover:bg-bg-sunken"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {isLoading && <p className="text-xs text-fg-dim">{t("common.loading")}</p>}
      {!isLoading && countries.length === 0 && (
        <p className="text-xs text-fg-dim">{t("feed.countryPanelEmpty")}</p>
      )}

      {!isLoading && countries.length > 0 && (
        <div className="flex flex-wrap gap-1.5" role="list" aria-label={t("feed.countryPanelTitle")}>
          {countries.map((c) => {
            const opt = countryOption(c.country);
            const active = selected.includes(c.country);
            return (
              <button
                key={c.country}
                type="button"
                role="listitem"
                onClick={() => onToggle(c.country)}
                aria-pressed={active}
                data-testid={`country-row-${c.country}`}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs",
                  active ? "border-accent bg-accent-muted text-accent-fg" : "border-border-strong hover:bg-bg-sunken",
                )}
              >
                <span aria-hidden="true">{opt.flag}</span>
                <bdi>{locale === "he" ? opt.nameHe : opt.nameEn}</bdi>
                <span className="font-mono font-tabular text-fg-dim">{c.total}</span>
                <span className="flex gap-0.5 font-mono" aria-hidden="true">
                  {c.red > 0 && <span className="text-level-red">{c.red}</span>}
                  {c.orange > 0 && <span className="text-level-orange">{c.orange}</span>}
                  {c.yellow > 0 && <span className="text-level-yellow">{c.yellow}</span>}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
