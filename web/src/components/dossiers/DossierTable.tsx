import type { ReactNode } from "react";
import { EmptyState } from "@/components/states";
import { cn } from "@/lib/cn";

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
  if (import.meta.env.DEV && columns.length > 6) {
    // eslint-disable-next-line no-console
    console.warn(`DossierTable "${caption}": ${columns.length} columns exceeds the 6-column limit`);
  }
  if (rows.length === 0) {
    return <EmptyState title={emptyLabel} />;
  }
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-border">
      <table className="w-full min-w-max text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-bg-raised">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className="whitespace-nowrap border-b border-border px-2.5 py-1.5 text-start text-xs font-semibold text-fg-dim"
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} className="hover:bg-bg-sunken">
              {columns.map((c) => (
                <td key={c.key} className={cn("px-2.5 py-1.5 align-top", c.className)}>
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
