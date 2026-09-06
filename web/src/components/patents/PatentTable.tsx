import { Fragment } from "react";
import type { PatentRecord } from "@/types/api";
import { ValueScorePopover } from "./ValueScorePopover";

export function PatentTable({
  patents,
  expandedId,
  onToggleExpand,
}: {
  patents: PatentRecord[];
  expandedId: number | null;
  onToggleExpand: (id: number) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[900px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-bg-raised text-fg-dim">
            <th className="p-2 text-start font-medium">מספר פרסום</th>
            <th className="p-2 text-start font-medium">כותרת</th>
            <th className="p-2 text-start font-medium">בעלים</th>
            <th className="p-2 text-start font-medium">תת-תחום</th>
            <th className="p-2 text-center font-medium">רלוונטיות לישראל</th>
            <th className="p-2 text-center font-medium">ציון-ערך</th>
            <th className="p-2 text-start font-medium">תאריך פרסום</th>
          </tr>
        </thead>
        <tbody>
          {patents.map((p) => {
            const isExpanded = expandedId === p.id;
            return (
              <Fragment key={p.id}>
                <tr
                  onClick={() => onToggleExpand(p.id)}
                  className="cursor-pointer border-b border-border last:border-0 hover:bg-bg-sunken"
                >
                  <td className="p-2 font-mono text-xs" dir="ltr">
                    {p.url ? (
                      <a
                        href={p.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-accent hover:underline"
                      >
                        {p.pub_number}
                      </a>
                    ) : (
                      p.pub_number
                    )}
                  </td>
                  <td className="p-2 font-medium text-fg">{p.title || "—"}</td>
                  <td className="p-2 text-fg-muted">{p.assignees.join(", ") || "—"}</td>
                  <td className="p-2 text-fg-muted">{p.subdomain || "—"}</td>
                  <td className="p-2 text-center">
                    {p.israel_relevance != null && p.israel_relevance >= 0.5 ? (
                      <span className="rounded-md bg-accent-muted px-2 py-0.5 text-xs text-fg">ישראל</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="p-2 text-center">
                    <ValueScorePopover score={p.value_score} reasons={p.value_reasons} />
                  </td>
                  <td className="p-2 text-fg-muted">{p.publication_date ?? "—"}</td>
                </tr>
                {isExpanded && (
                  <tr className="border-b border-border bg-bg-sunken last:border-0">
                    <td colSpan={7} className="p-3 text-sm">
                      <p className="mb-1 font-semibold text-fg">סיכום תביעות</p>
                      <p className="mb-2 leading-relaxed text-fg-muted" dir="auto">
                        {p.claims_summary_he || "טרם נותח."}
                      </p>
                      <p className="mb-1 font-semibold text-fg">משמעות (So What)</p>
                      <p className="leading-relaxed text-fg-muted" dir="auto">
                        {p.so_what_he || "טרם נותח."}
                      </p>
                      {p.cpc.length > 0 && (
                        <p className="mt-2 font-mono text-xs text-fg-dim">CPC: {p.cpc.join(", ")}</p>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
