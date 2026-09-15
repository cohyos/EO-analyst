import { useState } from "react";
import { ChevronDown, Layers } from "lucide-react";
import { PRODUCT_LINE_CATALOG, productLineLabel } from "@/lib/productLines";
import { cn } from "@/lib/cn";
import { useI18n, useT } from "@/i18n";
import { usePopoverEdgeClamp } from "@/hooks/usePopoverEdgeClamp";

/**
 * PL-ui (2026-09-07): "קו מוצר" multi-select filter, shared by the Feed and Tenders pages.
 * Mirrors `FeedFilters`' own country-filter popover 1:1 (chip button + checkbox popover) so it
 * reads as the same control family rather than a bolted-on new widget.
 *
 * Both pages apply this client-side (see the `productLines` doc comment in `FeedFiltersState`/
 * `TenderFiltersState`) -- the frozen API contract has no server-side `product_lines` query param
 * for `GET /api/items`/`GET /api/tenders`, only the additive `product_lines` field on each row.
 */
export function ProductLineFilter({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const t = useT();
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  const { triggerRef, popoverStyle } = usePopoverEdgeClamp<HTMLButtonElement>(open);

  function toggle(id: string) {
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  }

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={t("productLines.filterLabel")}
        data-testid="product-line-filter-toggle"
        className={cn(
          "flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-sm",
          value.length > 0
            ? "border-accent text-accent"
            : "border-border-strong text-fg-muted hover:bg-bg-sunken",
        )}
      >
        <Layers size={14} aria-hidden="true" />
        <span>
          {value.length > 0
            ? value.map((id) => productLineLabel(id, locale)).join(", ")
            : t("productLines.filterLabel")}
        </span>
        <ChevronDown size={12} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="group"
          aria-label={t("productLines.filterLabel")}
          data-testid="product-line-filter-menu"
          className="absolute top-full z-20 mt-1 max-h-72 w-[min(18rem,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-md border border-border-strong bg-bg-raised p-1.5 shadow-panel"
          style={popoverStyle}
        >
          {value.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="mb-1 w-full rounded px-2 py-1 text-start text-xs text-accent hover:bg-bg-sunken"
            >
              {t("productLines.filterClear")}
            </button>
          )}
          {PRODUCT_LINE_CATALOG.map((p) => {
            const active = value.includes(p.id);
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => toggle(p.id)}
                aria-pressed={active}
                className={cn(
                  "flex w-full items-center gap-2 rounded px-2 py-1 text-start text-sm",
                  active ? "bg-accent-muted text-accent-fg" : "hover:bg-bg-sunken",
                )}
              >
                <bdi className="truncate">{locale === "he" ? p.nameHe : p.nameEn}</bdi>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
