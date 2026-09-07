import { Search } from "lucide-react";
import { subdomainLabel } from "@/lib/taxonomy";

export interface PatentFiltersState {
  assignee: string;
  subdomain: string;
  israeli: boolean;
  min_value_score: string;
  q: string;
}

export function PatentFilters({
  value,
  onChange,
  assignees,
  subdomains,
}: {
  value: PatentFiltersState;
  onChange: (next: PatentFiltersState) => void;
  assignees: string[];
  subdomains: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <label className="flex items-center gap-1.5 text-sm text-fg-dim">
        <input
          type="checkbox"
          checked={value.israeli}
          onChange={(e) => onChange({ ...value, israeli: e.target.checked })}
        />
        רלוונטי לישראל בלבד
      </label>

      <select
        value={value.assignee}
        onChange={(e) => onChange({ ...value, assignee: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי בעלים"
      >
        <option value="">כל הבעלים</option>
        {assignees.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>

      <select
        value={value.subdomain}
        onChange={(e) => onChange({ ...value, subdomain: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי תת-תחום"
      >
        <option value="">כל תתי-התחום</option>
        {subdomains.map((s) => (
          <option key={s} value={s}>
            {subdomainLabel(s)}
          </option>
        ))}
      </select>

      <input
        type="number"
        min={0}
        max={100}
        value={value.min_value_score}
        onChange={(e) => onChange({ ...value, min_value_score: e.target.value })}
        placeholder="ציון-ערך מינ׳"
        className="w-28 rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-dim"
        aria-label="סינון לפי ציון-ערך מינימלי"
      />

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
          placeholder="חיפוש כותרת/תקציר…"
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label="חיפוש בפטנטים"
        />
      </div>
    </div>
  );
}
