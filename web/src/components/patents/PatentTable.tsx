import { ContentShareActions } from "@/components/ContentShareActions";
import { Fragment } from "react";
import type { PatentRecord } from "@/types/api";
import { ValueScorePopover } from "./ValueScorePopover";
import { subdomainLabel } from "@/lib/taxonomy";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useIsNarrowViewport } from "@/hooks/useIsNarrowViewport";

// Round-2 mobile fix (UI-MOBILE-iphone.md #6): factored out of the row so both the desktop
// table's expanded `<tr>` and the `<md:` card list's expanded panel render identical content from
// one implementation, same pattern as `TenderTable`'s `TenderDetailContent` (round 1, #5).
function PatentDetailContent({ p }: { p: PatentRecord }) {
  return (
    <>
      <ContentShareActions title={p.title || p.pub_number} links={[{ url: p.url, title: p.pub_number }]} />
      <p className="mb-1 font-semibold text-fg">סיכום תביעות</p>
      <p className="mb-2 leading-relaxed text-fg-muted" dir="auto">
        {p.claims_summary_he || "טרם נותח."}
      </p>
      <p className="mb-1 font-semibold text-fg">משמעות (So What)</p>
      <p className="leading-relaxed text-fg-muted" dir="auto">
        {p.so_what_he || "טרם נותח."}
      </p>
      {p.cpc.length > 0 && <p className="mt-2 font-mono text-xs text-fg-dim">CPC: {p.cpc.join(", ")}</p>}
    </>
  );
}

// Round-2 mobile fix (UI-MOBILE-iphone.md #6): the assignee column sat mid-table, cut off at the
// screen edge on a phone ("OVANCED SYSTEMS LTD [IL]"), and the title column was squeezed to a
// narrow 4-line wrap. Below `md`, this card list replaces the table entirely -- title on top
// (`line-clamp-3`), then pub number / assignee / date / CPC as a wrapping chip row, with the same
// click target (row toggles the expanded claims/so-what panel) and the same link target (the pub
// number still opens `p.url`) as the table.
function PatentMobileCard({
  p,
  expanded,
  onToggleExpand,
}: {
  p: PatentRecord;
  expanded: boolean;
  onToggleExpand: (id: number) => void;
}) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div
        onClick={() => onToggleExpand(p.id)}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggleExpand(p.id);
          }
        }}
        className="cursor-pointer p-3 hover:bg-bg-sunken"
      >
        <p className="line-clamp-3 text-sm font-medium text-fg">{p.title || "—"}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
          <span className="rounded-full bg-bg-sunken px-1.5 py-0.5 font-mono" dir="ltr">
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
          </span>
          {p.assignees.length > 0 && (
            <bdi className="max-w-[10rem] truncate rounded-full bg-bg-sunken px-1.5 py-0.5">
              {p.assignees.join(", ")}
            </bdi>
          )}
          <span className="rounded-full bg-bg-sunken px-1.5 py-0.5">{formatDate(p.publication_date)}</span>
          {p.cpc.length > 0 && (
            <span className="max-w-[10rem] truncate rounded-full bg-bg-sunken px-1.5 py-0.5 font-mono">
              {p.cpc.join(", ")}
            </span>
          )}
          {p.israel_relevance != null && p.israel_relevance >= 0.5 && (
            <span className="rounded-full bg-accent-muted px-1.5 py-0.5 text-fg">ישראל</span>
          )}
          <ValueScorePopover score={p.value_score} reasons={p.value_reasons} />
        </div>
      </div>
      {expanded && (
        <div data-share-content className="border-t border-border bg-bg-sunken p-3 text-sm">
          <PatentDetailContent p={p} />
        </div>
      )}
    </div>
  );
}

export function PatentTable({
  patents,
  expandedId,
  onToggleExpand,
}: {
  patents: PatentRecord[];
  expandedId: number | null;
  onToggleExpand: (id: number) => void;
}) {
  // Round-2 mobile fix (UI-MOBILE-iphone.md #6): picked in JS (not a CSS `md:hidden` /
  // `hidden md:block` pair on both variants), same reasoning as `TenderTable` round 1 (#5) --
  // rendering both and hiding one with CSS would double up every interactive element (the pub
  // number link, the value-score popover) in the accessibility tree and in tests.
  const isMobile = useIsNarrowViewport(768);
  if (isMobile) {
    return (
      <div className="rounded-lg border border-border">
        {patents.map((p) => (
          <PatentMobileCard
            key={p.id}
            p={p}
            expanded={expandedId === p.id}
            onToggleExpand={onToggleExpand}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[900px] border-collapse text-sm">
        <thead>
          <tr className="sticky top-0 z-10 border-b border-border bg-bg-raised text-fg-dim">
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
          {patents.map((p, i) => {
            const isExpanded = expandedId === p.id;
            return (
              <Fragment key={p.id}>
                <tr
                  onClick={() => onToggleExpand(p.id)}
                  className={cn(
                    "cursor-pointer border-b border-border last:border-0 hover:bg-bg-sunken",
                    i % 2 === 1 && "bg-bg-sunken/40",
                  )}
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
                  <td className="p-2 text-fg-muted">
                    <span className="block max-w-[14rem] truncate" title={subdomainLabel(p.subdomain)}>
                      {subdomainLabel(p.subdomain)}
                    </span>
                  </td>
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
                  <td className="p-2 text-fg-muted">{formatDate(p.publication_date)}</td>
                </tr>
                {isExpanded && (
                  <tr className="border-b border-border bg-bg-sunken last:border-0">
                    <td data-share-content colSpan={7} className="p-3 text-sm">
                      <PatentDetailContent p={p} />
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
