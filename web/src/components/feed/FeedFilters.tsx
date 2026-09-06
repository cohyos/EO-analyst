import { useState } from "react";
import { ChevronDown, Globe2, Search } from "lucide-react";
import type { TriageLevel } from "@/types/api";
import { LEVEL_META } from "@/components/LevelBadge";
import { DOMAIN_OPTIONS } from "@/lib/taxonomy";
import { COUNTRY_CATALOG, OTHER_COUNTRY, countryLabel } from "@/lib/countries";
import { cn } from "@/lib/cn";
import { useI18n, useT } from "@/i18n";

const ALL_LEVELS: TriageLevel[] = ["red", "orange", "yellow", "archive"];

export interface FeedFiltersState {
  levels: TriageLevel[];
  domain: string;
  q: string;
  sort: "score" | "published_at";
  /** U7b: selected ISO-2/region codes (empty = all countries). */
  countries: string[];
  /** U7b: group the feed rows under per-country headers. */
  groupByCountry: boolean;
  /** A13 (מיקוד תעשייה ישראלית): filter to items with `israel_relevance >= 0.5`. */
  israel: boolean;
}

export function FeedFilters({
  value,
  onChange,
}: {
  value: FeedFiltersState;
  onChange: (next: FeedFiltersState) => void;
}) {
  const t = useT();
  const { locale } = useI18n();
  const [countryMenuOpen, setCountryMenuOpen] = useState(false);

  function toggleLevel(level: TriageLevel) {
    const has = value.levels.includes(level);
    onChange({
      ...value,
      levels: has ? value.levels.filter((l) => l !== level) : [...value.levels, level],
    });
  }

  function toggleCountry(code: string) {
    const has = value.countries.includes(code);
    onChange({
      ...value,
      countries: has ? value.countries.filter((c) => c !== code) : [...value.countries, code],
    });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <div className="flex items-center gap-1" role="group" aria-label={t("feed.filterByLevelAria")}>
        {ALL_LEVELS.map((level) => {
          const meta = LEVEL_META[level];
          const active = value.levels.includes(level);
          return (
            <button
              key={level}
              type="button"
              onClick={() => toggleLevel(level)}
              aria-pressed={active}
              className={cn(
                "flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                active
                  ? cn(meta.bg, meta.fg, "border-transparent")
                  : "border-border-strong text-fg-dim hover:bg-bg-sunken",
              )}
            >
              {t(meta.labelKey)}
            </button>
          );
        })}
      </div>

      {/* A13 (מיקוד תעשייה ישראלית): additive boolean chip, same toggle-button UX as the level
          chips above — flips `israel` in FeedFiltersState, which FeedPage forwards as
          `israel=true` to GET /api/items. */}
      <button
        type="button"
        onClick={() => onChange({ ...value, israel: !value.israel })}
        aria-pressed={value.israel}
        data-testid="israel-filter-toggle"
        className={cn(
          "flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition-colors",
          value.israel
            ? "border-accent bg-accent-muted text-accent-fg"
            : "border-border-strong text-fg-dim hover:bg-bg-sunken",
        )}
      >
        {t("feed.israelFilterLabel")}
      </button>

      <select
        value={value.domain}
        onChange={(e) => onChange({ ...value, domain: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label={t("feed.filterByDomainAria")}
      >
        <option value="">{t("feed.allDomains")}</option>
        {DOMAIN_OPTIONS.map((d) => (
          <option key={d.id} value={d.id}>
            {d.label}
          </option>
        ))}
      </select>

      {/* U7b: country filter chip-set, collapsed into a popover so it doesn't
          crowd the level/domain filters when nothing is selected. */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setCountryMenuOpen((v) => !v)}
          aria-expanded={countryMenuOpen}
          aria-label={t("feed.countryFilterLabel")}
          data-testid="country-filter-toggle"
          className={cn(
            "flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-sm",
            value.countries.length > 0
              ? "border-accent text-accent"
              : "border-border-strong text-fg-muted hover:bg-bg-sunken",
          )}
        >
          <Globe2 size={14} aria-hidden="true" />
          <span>
            {value.countries.length > 0
              ? value.countries.map((c) => countryLabel(c, locale)).join(", ")
              : t("feed.countryFilterLabel")}
          </span>
          <ChevronDown size={12} aria-hidden="true" />
        </button>
        {countryMenuOpen && (
          <div
            role="group"
            aria-label={t("feed.countryFilterLabel")}
            data-testid="country-filter-menu"
            className="absolute top-full z-20 mt-1 max-h-72 w-64 overflow-y-auto rounded-md border border-border-strong bg-bg-raised p-1.5 shadow-panel"
            style={{ insetInlineStart: 0 }}
          >
            {value.countries.length > 0 && (
              <button
                type="button"
                onClick={() => onChange({ ...value, countries: [] })}
                className="mb-1 w-full rounded px-2 py-1 text-start text-xs text-accent hover:bg-bg-sunken"
              >
                {t("feed.allCountries")}
              </button>
            )}
            {[...COUNTRY_CATALOG, OTHER_COUNTRY].map((c) => {
              const active = value.countries.includes(c.code);
              return (
                <button
                  key={c.code}
                  type="button"
                  onClick={() => toggleCountry(c.code)}
                  aria-pressed={active}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2 py-1 text-start text-sm",
                    active ? "bg-accent-muted text-accent-fg" : "hover:bg-bg-sunken",
                  )}
                >
                  <span aria-hidden="true">{c.flag}</span>
                  <bdi className="truncate">{locale === "he" ? c.nameHe : c.nameEn}</bdi>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <label className="flex items-center gap-1.5 text-sm text-fg-muted">
        <input
          type="checkbox"
          checked={value.groupByCountry}
          onChange={(e) => onChange({ ...value, groupByCountry: e.target.checked })}
          className="h-3.5 w-3.5 accent-accent"
        />
        {t("feed.groupByCountry")}
      </label>

      <select
        value={value.sort}
        onChange={(e) => onChange({ ...value, sort: e.target.value as FeedFiltersState["sort"] })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label={t("feed.sortAria")}
      >
        <option value="score">{t("feed.sortByScore")}</option>
        <option value="published_at">{t("feed.sortByDate")}</option>
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
          placeholder={t("feed.searchPlaceholder")}
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label={t("feed.searchAria")}
        />
      </div>
    </div>
  );
}
