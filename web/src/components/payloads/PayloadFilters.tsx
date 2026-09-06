import { Search } from "lucide-react";
import type { PayloadCategory } from "@/types/api";

export interface PayloadFiltersState {
  category: PayloadCategory | "";
  vendor: string;
  q: string;
}

const CATEGORY_LABELS_HE: Record<PayloadCategory, string> = {
  gimbal: "גימבל",
  pod: "פוד",
  thermal_camera: "מצלמה תרמית",
  detector_core: "גלעין גלאי",
  lrf: "LRF",
  seeker: "ראש ביות",
  other: "אחר",
};

export function PayloadFilters({
  value,
  onChange,
  vendors,
}: {
  value: PayloadFiltersState;
  onChange: (next: PayloadFiltersState) => void;
  vendors: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-bg-raised p-3">
      <select
        value={value.category}
        onChange={(e) => onChange({ ...value, category: e.target.value as PayloadFiltersState["category"] })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי קטגוריה"
      >
        <option value="">כל הקטגוריות</option>
        {(Object.keys(CATEGORY_LABELS_HE) as PayloadCategory[]).map((c) => (
          <option key={c} value={c}>
            {CATEGORY_LABELS_HE[c]}
          </option>
        ))}
      </select>

      <select
        value={value.vendor}
        onChange={(e) => onChange({ ...value, vendor: e.target.value })}
        className="rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm text-fg"
        aria-label="סינון לפי יצרן"
      >
        <option value="">כל היצרנים</option>
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
          placeholder="חיפוש שם/משפחת מוצרים…"
          className="w-full rounded-md border border-border-strong bg-bg py-1.5 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "1.75rem", paddingInlineEnd: "0.5rem" }}
          aria-label={'חיפוש במטע"דים'}
        />
      </div>

      <a
        href="/api/payloads/export.csv"
        target="_blank"
        rel="noreferrer"
        className="rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg hover:bg-bg-sunken"
      >
        ייצוא CSV
      </a>
    </div>
  );
}

export { CATEGORY_LABELS_HE };
