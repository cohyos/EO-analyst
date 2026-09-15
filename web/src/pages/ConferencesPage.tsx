import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, CalendarPlus, ChevronDown, Download, ExternalLink } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { TableScrollHint } from "@/components/TableScrollHint";
import { formatDate } from "@/lib/time";
import { downloadConferenceIcs } from "@/lib/ics";
import { cn } from "@/lib/cn";
import type { Conference } from "@/types/api";

const STATUS_LABEL: Record<string, string> = {
  confirmed: "מאושר",
  estimated: "משוער",
  cancelled: "בוטל",
};

const FIELD_LABEL: Record<string, string> = {
  start_date: "תאריך התחלה",
  end_date: "תאריך סיום",
  registration_url: "קישור הרשמה",
  registration_opens: "פתיחת הרשמה",
  early_bird_deadline: "מועד early bird",
  cfp_deadline: "מועד CFP",
  cost_range: "טווח עלות",
  status: "סטטוס",
  venue: "מקום",
  city: "עיר",
};

function ConferenceDetailRow({ c }: { c: Conference }) {
  const changeEntries = Object.entries(c.changes ?? {});
  return (
    <tr className="border-t border-border bg-bg-sunken/60">
      <td colSpan={6} className="p-3 text-xs">
        <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <span className="text-fg-dim">פתיחת הרשמה: </span>
            <span className="font-mono">{formatDate(c.registration_opens) || "—"}</span>
          </div>
          <div>
            <span className="text-fg-dim">Early bird: </span>
            <span className="font-mono">{formatDate(c.early_bird_deadline) || "—"}</span>
          </div>
          <div>
            <span className="text-fg-dim">מועד CFP: </span>
            <span className="font-mono">{formatDate(c.cfp_deadline) || "—"}</span>
          </div>
          <div>
            <span className="text-fg-dim">טווח עלות: </span>
            <bdi>{c.cost_range ?? "—"}</bdi>
          </div>
          <div>
            <span className="text-fg-dim">סטטוס: </span>
            <bdi>{c.status ? (STATUS_LABEL[c.status] ?? c.status) : "—"}</bdi>
          </div>
          <div>
            <span className="text-fg-dim">אומת לאחרונה: </span>
            <span className="font-mono">{formatDate(c.last_verified_at) || "—"}</span>
          </div>
        </div>

        {c.entry_conditions && (
          <p className="mt-2">
            <span className="text-fg-dim">תנאי כניסה: </span>
            <bdi dir="auto">{c.entry_conditions}</bdi>
          </p>
        )}
        {c.rationale && (
          <p className="mt-2">
            <span className="text-fg-dim">נימוק: </span>
            <bdi dir="auto">{c.rationale}</bdi>
          </p>
        )}

        {changeEntries.length > 0 && (
          <div className="mt-2">
            <p className="mb-1 font-semibold text-fg-dim">שינויים מהסריקה הקודמת</p>
            <ul className="space-y-0.5">
              {changeEntries.map(([field, ch]) => (
                <li key={field}>
                  <span className="text-fg-dim">{FIELD_LABEL[field] ?? field}: </span>
                  <span className="text-danger line-through">{String(ch.from ?? "—")}</span>
                  {" → "}
                  <span className="text-ok">{String(ch.to ?? "—")}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </td>
    </tr>
  );
}

export function ConferencesPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["conferences"],
    queryFn: () => api.getConferences(),
  });
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (isLoading) return <LoadingState label="טוען לוח כנסים…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;

  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<CalendarClock size={26} aria-hidden="true" />}
        title="אין כנסים קרובים"
        description="לוח הכנסים מתעדכן בסריקה החודשית (FR-12); אין כרגע רשומות בטווח."
      />
    );
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex justify-end">
        <a
          href={api.getConferencesIcalUrl()}
          className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg-muted hover:bg-bg-sunken"
        >
          <Download size={14} aria-hidden="true" />
          ייצוא iCal (כל הכנסים)
        </a>
      </div>
      <div className="overflow-x-auto rounded-lg border border-border" dir="rtl">
        <TableScrollHint />
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-bg-raised text-xs text-fg-dim">
            <tr>
              <th className="p-2 text-start"></th>
              {/* Round-4 mobile fix (fix #3): sticky-first-column treatment -- the leading chevron
                  column is a narrow, icon-only toggle (not the row's identifying label), so the
                  name column is the one pinned via `sticky start-0` while scrolling the date/
                  relevance/actions columns sideways on a phone, same contract as RadarMatrix's
                  row-label column. */}
              <th className="sticky start-0 z-10 border-e border-border bg-bg-raised p-2 text-start">
                שם
              </th>
              <th className="p-2 text-start">מיקום</th>
              <th className="p-2 text-start">מתחיל</th>
              <th className="p-2 text-start">מסתיים</th>
              <th className="p-2 text-start">רלוונטיות</th>
              <th className="p-2 text-start">פעולות</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c) => {
              const outUrl = c.registration_url || c.url;
              const expanded = expandedId === c.id;
              return (
                <Fragment key={c.id}>
                  <tr
                    onClick={() => setExpandedId(expanded ? null : c.id)}
                    className="cursor-pointer border-t border-border hover:bg-bg-sunken"
                    aria-expanded={expanded}
                  >
                    <td className="p-2 text-fg-dim">
                      <ChevronDown
                        size={14}
                        className={cn("transition-transform", expanded && "rotate-180")}
                        aria-hidden="true"
                      />
                    </td>
                    <td className="sticky start-0 z-[1] border-e border-border bg-bg p-2">
                      {outUrl ? (
                        // The name itself is plain text (part of the row's click-to-expand
                        // surface, below) with only a small icon-button carrying the outbound
                        // link -- previously the whole name (often the widest thing in the row)
                        // was wrapped in the `<a>`. On narrow viewports the table is wider than
                        // the screen (min-w-[720px] in an overflow-x-auto strip) and the summary
                        // row's *unscrolled* visible slice is exactly this leading (chevron+name)
                        // portion, so a tap anywhere in that slice -- including a plain center
                        // tap meant to expand the row -- landed on the full-width name link and
                        // opened it instead of toggling `aria-expanded` (07-conferences.spec.ts,
                        // mobile-390x844 + iphone-safari, R6-ui). Shrinking the link to just the
                        // icon leaves the rest of the visible row (the name text) as plain,
                        // non-navigating surface a tap can land on to expand/collapse.
                        <span className="inline-flex min-w-0 items-center gap-1.5">
                          <bdi className="truncate" title={c.name}>
                            {c.name}
                          </bdi>
                          <a
                            href={outUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex shrink-0 items-center rounded p-0.5 text-fg-dim hover:bg-bg-sunken hover:text-accent"
                            title="פתח קישור הרשמה/מקור בכרטיסייה חדשה"
                            aria-label={`פתח קישור הרשמה עבור ${c.name}`}
                          >
                            <ExternalLink size={12} aria-hidden="true" />
                          </a>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5">
                          <bdi>{c.name}</bdi>
                          <span className="rounded-full bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
                            אין קישור
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="p-2 text-fg-muted">{c.location ?? "—"}</td>
                    <td className="p-2 font-mono">{formatDate(c.starts_at)}</td>
                    <td className="p-2 font-mono">{formatDate(c.ends_at)}</td>
                    <td className="p-2 text-fg-muted">
                      <bdi>{c.relevance_he ?? "—"}</bdi>
                    </td>
                    <td className="p-2">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          downloadConferenceIcs(c);
                        }}
                        className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-dim hover:bg-bg-sunken hover:text-fg"
                        title="הוסף ליומן (ICS)"
                      >
                        <CalendarPlus size={12} aria-hidden="true" />
                        הוסף ליומן
                      </button>
                    </td>
                  </tr>
                  {expanded && <ConferenceDetailRow c={c} />}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
