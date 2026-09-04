import { Search } from "lucide-react";
import type { TriageLevel } from "@/types/api";
import { LEVEL_META } from "@/components/LevelBadge";
import { DOMAIN_OPTIONS } from "@/lib/taxonomy";
import { cn } from "@/lib/cn";

const ALL_LEVELS: TriageLevel[] = ["red", "orange", "yellow", "archive"];

export interface FeedFiltersState {
  levels: TriageLevel[];
  domain: string;
  q: string;
  sort: "score" | "published_at";
}

export function FeedFilters({
  value,
  onChange,
}: {
  value: FeedFiltersState;
  onChange: (next: FeedFiltersState) => void;
}) {
  function toggleLevel(level: TriageLevel) {
    const has = value.levels.includes(level);
    onChange({
      ...value,
      levels: has ? value.levels.filter((l) => l !== level) : [...value.levels, level],
    });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <div className="flex items-center gap-1" role="group" aria-label="סינון לפי רמה">
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
              {meta.label}
            </button>
          );
        })}
      </div>

      <select
        value={value.domain}
        onChange={(e) => onChange({ ...value, domain: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי תחום"
      >
        <option value="">כל התחומים</option>
        {DOMAIN_OPTIONS.map((d) => (
          <option key={d.id} value={d.id}>
            {d.label}
          </option>
        ))}
      </select>

      <select
        value={value.sort}
        onChange={(e) => onChange({ ...value, sort: e.target.value as FeedFiltersState["sort"] })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="מיון"
      >
        <option value="score">מיון: ציון</option>
        <option value="published_at">מיון: תאריך</option>
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
          placeholder="חיפוש טקסט חופשי…"
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label="חיפוש בפיד"
        />
      </div>
    </div>
  );
}
