import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Download } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { formatDate } from "@/lib/time";

export function ConferencesPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["conferences"],
    queryFn: () => api.getConferences(),
  });

  if (isLoading) return <LoadingState label="טוען לוח כנסים…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;

  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<CalendarClock size={26} aria-hidden="true" />}
        title="לוח הכנסים יופעל בשלב ג'"
        description="תצוגת ציר הזמן והייצוא ל-iCal יהיו זמינים כשמודול הכנסים יושק."
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
          ייצוא iCal
        </a>
      </div>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-bg-raised text-xs text-fg-dim">
            <tr>
              <th className="p-2 text-start">שם</th>
              <th className="p-2 text-start">מיקום</th>
              <th className="p-2 text-start">מתחיל</th>
              <th className="p-2 text-start">מסתיים</th>
              <th className="p-2 text-start">רלוונטיות</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id} className="border-t border-border hover:bg-bg-sunken">
                <td className="p-2">
                  <bdi>{c.name}</bdi>
                </td>
                <td className="p-2 text-fg-muted">{c.location ?? "—"}</td>
                <td className="p-2 font-mono">{formatDate(c.starts_at)}</td>
                <td className="p-2 font-mono">{formatDate(c.ends_at)}</td>
                <td className="p-2 text-fg-muted">
                  <bdi>{c.relevance_he ?? "—"}</bdi>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
