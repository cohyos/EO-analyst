import { edgeLabelHe, entityKindLabel } from "@/components/entities/eventKindLabel";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { countryLabel } from "@/lib/countries";
import { PRODUCT_LINE_CATALOG } from "@/lib/productLines";
import { ENTITY_KIND_OPTIONS, RELATION_TYPE_OPTIONS } from "./graphColors";

export interface GraphFilterState {
  kinds: string[];
  relationTypes: string[];
  country: string;
  productLine: string;
  sinceDays: number | null;
  depth: 1 | 2;
}

export const DEFAULT_GRAPH_FILTERS: GraphFilterState = {
  kinds: [],
  relationTypes: [],
  country: "",
  productLine: "",
  sinceDays: null,
  depth: 1,
};

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

const SINCE_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: "כל הזמנים" },
  { value: 7, label: "7 ימים אחרונים" },
  { value: 30, label: "30 יום אחרונים" },
  { value: 90, label: "90 יום אחרונים" },
];

export function GraphFilterBar({
  filters,
  onChange,
  availableCountries,
}: {
  filters: GraphFilterState;
  onChange: (next: GraphFilterState) => void;
  availableCountries: string[];
}) {
  return (
    <div className="flex flex-col gap-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-fg-dim">סוג ישות:</span>
        {ENTITY_KIND_OPTIONS.map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => onChange({ ...filters, kinds: toggle(filters.kinds, k) })}
            aria-pressed={filters.kinds.includes(k)}
            className={`rounded-full border px-2 py-0.5 ${
              filters.kinds.includes(k)
                ? "border-accent bg-accent/15 text-accent"
                : "border-border-strong text-fg-muted"
            }`}
          >
            {entityKindLabel(k)}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-fg-dim">סוג קשר:</span>
        {RELATION_TYPE_OPTIONS.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() =>
              onChange({ ...filters, relationTypes: toggle(filters.relationTypes, r) })
            }
            aria-pressed={filters.relationTypes.includes(r)}
            className={`rounded-full border px-2 py-0.5 ${
              filters.relationTypes.includes(r)
                ? "border-accent bg-accent/15 text-accent"
                : "border-border-strong text-fg-muted"
            }`}
          >
            {edgeLabelHe(r)}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          <span className="text-fg-dim">מדינה:</span>
          <select
            value={filters.country}
            onChange={(e) => onChange({ ...filters, country: e.target.value })}
            className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
          >
            <option value="">הכל</option>
            {availableCountries.map((c) => (
              <option key={c} value={c}>
                {countryFlagEmoji(c)} {countryLabel(c, "he")}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-1">
          <span className="text-fg-dim">קו מוצר:</span>
          <select
            value={filters.productLine}
            onChange={(e) => onChange({ ...filters, productLine: e.target.value })}
            className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
          >
            <option value="">הכל</option>
            {PRODUCT_LINE_CATALOG.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nameHe}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-1">
          <span className="text-fg-dim">חלון זמן:</span>
          <select
            value={filters.sinceDays ?? ""}
            onChange={(e) =>
              onChange({
                ...filters,
                sinceDays: e.target.value === "" ? null : Number(e.target.value),
              })
            }
            className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
          >
            {SINCE_OPTIONS.map((s) => (
              <option key={s.label} value={s.value ?? ""}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-1">
          <span className="text-fg-dim">עומק:</span>
          <select
            value={filters.depth}
            onChange={(e) =>
              onChange({ ...filters, depth: Number(e.target.value) as 1 | 2 })
            }
            className="max-w-full min-w-0 rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
          >
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>

        {(filters.kinds.length > 0 ||
          filters.relationTypes.length > 0 ||
          filters.country ||
          filters.productLine ||
          filters.sinceDays != null) && (
          <button
            type="button"
            onClick={() => onChange(DEFAULT_GRAPH_FILTERS)}
            className="rounded-md border border-border-strong px-2 py-1 text-fg-muted hover:bg-bg-sunken"
          >
            נקה סינון
          </button>
        )}
      </div>
    </div>
  );
}
