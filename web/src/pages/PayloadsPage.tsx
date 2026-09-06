import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PayloadFilters, type PayloadFiltersState } from "@/components/payloads/PayloadFilters";
import { PayloadTable } from "@/components/payloads/PayloadTable";
import { PayloadDetailDrawer } from "@/components/payloads/PayloadDetailDrawer";

const EMPTY_FILTERS: PayloadFiltersState = { category: "", vendor: "", q: "" };

export function PayloadsPage() {
  const [filters, setFilters] = useState<PayloadFiltersState>(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const payloadsQuery = useQuery({
    queryKey: ["payloads", filters.category],
    queryFn: () => api.getPayloads({ category: filters.category || undefined, limit: 500 }),
  });

  const payloads = payloadsQuery.data?.payloads ?? [];

  const vendors = useMemo(() => {
    const set = new Set<string>();
    for (const p of payloads) if (p.vendor_entity_name) set.add(p.vendor_entity_name);
    return [...set].sort();
  }, [payloads]);

  const filteredPayloads = useMemo(() => {
    let list = payloads;
    if (filters.vendor) list = list.filter((p) => p.vendor_entity_name === filters.vendor);
    if (filters.q) {
      const q = filters.q.toLowerCase();
      list = list.filter(
        (p) => p.canonical_name.toLowerCase().includes(q) || (p.family ?? "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [payloads, filters.vendor, filters.q]);

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="rounded-md border border-border-strong bg-bg-raised px-3 py-2 text-sm text-fg-dim" dir="auto">
        {'תיעוד מפרטים ומחירי ייחוס של מטע"דים אלקטרו-אופטיים -- כל שינוי נשמר כגרסה נפרדת עם מקור מצוטט, ולעולם לא נדרס.'}
      </div>

      {payloadsQuery.isLoading && <LoadingState label={'טוען מטע"דים…'} />}
      {payloadsQuery.isError && <ErrorState onRetry={() => payloadsQuery.refetch()} />}

      {!payloadsQuery.isLoading && !payloadsQuery.isError && (
        <>
          {payloads.length === 0 ? (
            <EmptyState
              title={'לא זוהו מטע"דים עדיין'}
              description={'הסריקה הלילית (payload_extract) עדיין לא מצאה מפרט/מחיר מצוטט בפריטים שנאספו, או שהיא כבויה. ראו config/config.yaml: payloads.enabled.'}
            />
          ) : (
            <>
              <PayloadFilters value={filters} onChange={setFilters} vendors={vendors} />
              {filteredPayloads.length === 0 ? (
                <EmptyState title="אין תוצאות תואמות" description="נסה לשנות את הסינון." />
              ) : (
                <PayloadTable payloads={filteredPayloads} selectedId={selectedId} onSelect={setSelectedId} />
              )}
            </>
          )}
        </>
      )}

      {selectedId != null && <PayloadDetailDrawer payloadId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
