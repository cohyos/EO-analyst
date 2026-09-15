import type { ReactNode } from "react";
import { EmptyState } from "@/components/states";
import { cn } from "@/lib/cn";
import { useIsNarrowViewport } from "@/hooks/useIsNarrowViewport";

export interface DossierTableColumn<T> {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  className?: string;
}

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "every table <= 6 columns with sticky header
 * and horizontal scroll" -- shared across every dossier section table (specs, versions,
 * performance, deals, pricing, partnerships, competitors, patents, tenders, sources). The sticky
 * header is scoped to this component's own `max-h` scroll container (rather than relying on the
 * page's own scroll position) so it works the same regardless of where the table sits on the
 * page or how the surrounding layout scrolls.
 */
export function DossierTable<T>({
  columns,
  rows,
  rowKey,
  emptyLabel,
  caption,
}: {
  columns: DossierTableColumn<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  emptyLabel: string;
  /** Visually-hidden `<caption>` for screen readers -- names the table beyond its section
   * heading, which a table element doesn't otherwise get to hear announced. */
  caption: string;
}) {
  // Round-2 mobile fix (UI-MOBILE-iphone.md #3): the hook must run on every render regardless of
  // the early returns below (rules of hooks) -- it's cheap and unused work when the table ends up
  // empty or narrow-mode doesn't apply anyway.
  const isMobile = useIsNarrowViewport(768);

  if (import.meta.env.DEV && columns.length > 6) {
    // eslint-disable-next-line no-console
    console.warn(`DossierTable "${caption}": ${columns.length} columns exceeds the 6-column limit`);
  }
  if (rows.length === 0) {
    return <EmptyState title={emptyLabel} />;
  }
  // Mobile fix (UI-MOBILE-iphone.md #4): a 2-column key/value table -- the common case here
  // (specs, versions, pricing, ...) -- doesn't need horizontal scrolling at all once its cells
  // are allowed to wrap. Forcing `min-w-max` + `whitespace-nowrap` on *every* table, even a
  // 2-column one, is what made the dossier detail page a wall of horizontally-scrolling strips on
  // phones. Only tables wide enough to actually need it (>3 columns) keep the old min-w-max +
  // no-wrap behavior; those pin their first column via `sticky start-0` so the row's own label
  // stays visible while scrolling through the rest of a wide row.
  const isWide = columns.length > 3;

  // Round-2 mobile fix (UI-MOBILE-iphone.md #3): sticky-first-column wasn't enough -- a wide
  // (>3 column) table still scrolled horizontally on phones with the value column cut off
  // mid-word. Below `md`, a wide table renders as a stacked card list instead: the first column
  // as the card's bold title, the second ("main value") column full-width and wrapping, and every
  // remaining column as a `label: value` chip underneath -- same data, same `columns` contract, so
  // every consumer (DossierDetailPage's sections, DossierSpecTable, DossierComparisonView, the
  // compare page) gets this for free without its own mobile-specific markup. A narrow (<=3
  // column) table already wraps in place (above) and doesn't need this.
  if (isWide && isMobile) {
    const [titleCol, mainCol, ...restCols] = columns;
    // `relative`: Tailwind's `sr-only` is position:absolute; without a positioned ancestor the
    // caption escaped <main>'s scroll clip and stretched document.documentElement to the page's
    // full height (a second, phantom scroll layer on phones -- round-2 mobile audit).
    return (
      <div className="relative space-y-2" dir="rtl">
        <span className="sr-only">{caption}</span>
        {rows.map((row, i) => (
          <div
            key={rowKey(row, i)}
            className="rounded-lg border border-border bg-bg-raised p-3 text-sm"
          >
            <div className="font-semibold text-fg">{titleCol.render(row)}</div>
            {mainCol && (
              <div className="mt-1 whitespace-normal break-words text-fg">{mainCol.render(row)}</div>
            )}
            {restCols.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-dim">
                {restCols.map((c) => (
                  <span key={c.key} className="inline-flex flex-wrap items-center gap-1">
                    <span>{c.label}:</span>
                    <span className="text-fg">{c.render(row)}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-border" dir="rtl">
      <table className={cn("w-full text-sm", isWide && "min-w-max")}>
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-bg-raised">
          <tr>
            {columns.map((c, i) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "border-b border-border px-2.5 py-1.5 text-start text-xs font-semibold text-fg-dim",
                  isWide ? "whitespace-nowrap" : "whitespace-normal break-words",
                  // The header's top-sticky bg (on <thead>) doesn't extend to a horizontally-sticky
                  // corner cell -- it needs its own opaque bg to occlude columns scrolling under it.
                  isWide && i === 0 && "sticky start-0 z-20 bg-bg-raised",
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} className="hover:bg-bg-sunken">
              {columns.map((c, ci) => (
                <td
                  key={c.key}
                  className={cn(
                    "px-2.5 py-1.5 align-top",
                    isWide ? "whitespace-nowrap" : "whitespace-normal break-words",
                    isWide && ci === 0 && "sticky start-0 z-[1] bg-bg",
                    c.className,
                  )}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
