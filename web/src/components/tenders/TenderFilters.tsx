import { Search } from "lucide-react";
import type { TenderStatus } from "@/types/api";
import { TENDER_STATUS_LABEL } from "@/lib/tenders";

const ALL_STATUSES: TenderStatus[] = ["open", "closed", "awarded", "unknown"];

export interface TenderFiltersState {
  status: TenderStatus | "";
  country: string;
  q: string;
}

export function TenderFilters({
  value,
  onChange,
  countries,
}: {
  value: TenderFiltersState;
  onChange: (next: TenderFiltersState) => void;
  countries: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <select
        value={value.status}
        onChange={(e) => onChange({ ...value, status: e.target.value as TenderStatus | "" })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי סטטוס"
      >
        <option value="">כל הסטטוסים</option>
        {ALL_STATUSES.map((s) => (
          <option key={s} value={s}>
            {TENDER_STATUS_LABEL[s]}
          </option>
        ))}
      </select>

      <select
        value={value.country}
        onChange={(e) => onChange({ ...value, country: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי מדינה"
      >
        <option value="">כל המדינות</option>
        {countries.map((c) => (
          <option key={c} value={c}>
            {c}
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
          placeholder="חיפוש כותרת, גוף מזמין או מונח…"
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label="חיפוש במכרזים"
        />
      </div>
    </div>
  );
}
