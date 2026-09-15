import type { TechMaturity, TechRadarSubdomain } from "@/types/api";
import { cn } from "@/lib/cn";
import { Sparkline } from "./Sparkline";
import { TableScrollHint } from "@/components/TableScrollHint";

const MATURITY_LABEL_HE: Record<TechMaturity, string> = {
  lab: "מעבדה",
  prototype: "אב-טיפוס",
  qualified: "מוסמך",
  fielded: "מבצעי",
};

interface RadarMatrixProps {
  subdomains: TechRadarSubdomain[];
  maturities: TechMaturity[];
  selectedSubdomain: string | null;
  selectedMaturity: TechMaturity | null;
  onCellClick: (subdomain: string | null, maturity: TechMaturity | null) => void;
}

/** A12 (מעקב טכנולוגי): subdomain x maturity count matrix -- click a cell (or a row's total) to
 * filter the items list below it. */
export function RadarMatrix({
  subdomains,
  maturities,
  selectedSubdomain,
  selectedMaturity,
  onCellClick,
}: RadarMatrixProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border" dir="rtl">
      <TableScrollHint />
      <table className="w-full min-w-[720px] border-collapse text-sm">
        {/* Round-4 mobile fix (fix #3): the row label ("תת-תחום") pinned via `sticky start-0` so
            it stays visible while scrolling the maturity columns sideways on a phone -- same
            sticky-corner-cell contract DossierTable already uses (own opaque bg + higher z-index
            so it occludes columns scrolling underneath it). The whole header row is `sticky
            top-0` too (the page's actual scroll container is AppShell's `<main
            overflow-y-auto>`, not the window, so this pins correctly under the app's own top bar
            rather than the browser chrome). */}
        <thead>
          <tr className="border-b border-border bg-bg-raised text-fg-dim">
            <th className="sticky start-0 top-0 z-20 border-e border-border bg-bg-raised p-2 text-start font-medium">
              תת-תחום
            </th>
            {maturities.map((m) => (
              <th key={m} className="sticky top-0 z-10 bg-bg-raised p-2 text-center font-medium">
                {MATURITY_LABEL_HE[m]}
              </th>
            ))}
            <th className="sticky top-0 z-10 bg-bg-raised p-2 text-center font-medium">סה"כ</th>
            <th className="sticky top-0 z-10 bg-bg-raised p-2 text-center font-medium">
              מגמה (4 שבועות)
            </th>
          </tr>
        </thead>
        <tbody>
          {subdomains.map((sub) => (
            <tr key={sub.subdomain} className="border-b border-border last:border-0">
              <td className="sticky start-0 z-[1] border-e border-border bg-bg p-2 font-medium text-fg">
                {sub.label_he}
              </td>
              {maturities.map((m) => {
                const count = sub.counts[m] ?? 0;
                const isSelected = selectedSubdomain === sub.subdomain && selectedMaturity === m;
                return (
                  <td key={m} className="p-1 text-center">
                    <button
                      type="button"
                      data-testid={`radar-cell-${sub.subdomain}-${m}`}
                      onClick={() => onCellClick(sub.subdomain, isSelected ? null : m)}
                      disabled={count === 0}
                      aria-pressed={isSelected}
                      className={cn(
                        "min-w-10 rounded-md px-2 py-1 font-mono font-tabular",
                        count === 0 ? "text-fg-dim/40" : "text-fg hover:bg-bg-sunken",
                        isSelected && "bg-accent-muted text-fg ring-1 ring-accent",
                      )}
                    >
                      {count}
                    </button>
                  </td>
                );
              })}
              <td className="p-1 text-center">
                <button
                  type="button"
                  data-testid={`radar-total-${sub.subdomain}`}
                  onClick={() =>
                    onCellClick(
                      selectedSubdomain === sub.subdomain && selectedMaturity === null ? null : sub.subdomain,
                      null,
                    )
                  }
                  disabled={sub.total === 0}
                  className={cn(
                    "min-w-10 rounded-md px-2 py-1 font-mono font-tabular font-semibold",
                    sub.total === 0 ? "text-fg-dim/40" : "text-fg hover:bg-bg-sunken",
                    selectedSubdomain === sub.subdomain && selectedMaturity === null && "bg-accent-muted ring-1 ring-accent",
                  )}
                >
                  {sub.total}
                </button>
              </td>
              <td className="p-1">
                <Sparkline values={sub.sparkline} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
