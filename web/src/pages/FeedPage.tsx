import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemCard, TriageLevel } from "@/types/api";
import { api } from "@/api";
import { FeedFilters, type FeedFiltersState } from "@/components/feed/FeedFilters";
import { FeedRow } from "@/components/feed/FeedRow";
import { FeedDetailPanel } from "@/components/feed/FeedDetailPanel";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useVirtualList } from "@/hooks/useVirtualList";
import { useUiStore } from "@/store/uiStore";

const ROW_HEIGHT = 64;

const LEVEL_BY_DIGIT: Record<string, TriageLevel> = {
  "1": "red",
  "2": "orange",
  "3": "yellow",
  "4": "archive",
};

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el as HTMLElement).isContentEditable;
}

export function FeedPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState<FeedFiltersState>({
    levels: [],
    domain: "",
    q: "",
    sort: "score",
  });
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [openItemId, setOpenItemId] = useState<number | null>(null);
  const addToChatContext = useUiStore((s) => s.addToChatContext);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["items", filters],
    queryFn: () =>
      api.getItems({
        level: filters.levels.length ? filters.levels : undefined,
        domain: filters.domain || undefined,
        q: filters.q || undefined,
        sort: filters.sort,
        page: 1,
        page_size: 100,
      }),
  });

  const items = useMemo(() => data?.items ?? [], [data]);

  useEffect(() => {
    const openParam = searchParams.get("open");
    if (openParam) {
      setOpenItemId(Number(openParam));
    }
  }, [searchParams]);

  useEffect(() => {
    if (selectedIndex >= items.length) setSelectedIndex(Math.max(0, items.length - 1));
  }, [items, selectedIndex]);

  const feedback = useMutation({
    mutationFn: ({ id, level }: { id: number; level: TriageLevel }) =>
      api.postItemFeedback(id, { user_level: level, comment: null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
  });

  const investigate = useMutation({
    mutationFn: (id: number) => api.postItemInvestigate(id, { question: null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["investigations"] }),
  });

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(document.activeElement)) return;
      const selected = items[selectedIndex];

      if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(items.length - 1, i + 1));
        return;
      }
      if (e.key === "k" || e.key === "K" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (!selected) return;
      if (["1", "2", "3", "4"].includes(e.key)) {
        e.preventDefault();
        feedback.mutate({ id: selected.id, level: LEVEL_BY_DIGIT[e.key] });
        return;
      }
      if (e.key === "x" || e.key === "X") {
        e.preventDefault();
        feedback.mutate({ id: selected.id, level: "archive" });
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        setOpenItemId(selected.id);
        return;
      }
      if (e.key === "i" || e.key === "I") {
        e.preventDefault();
        investigate.mutate(selected.id);
        return;
      }
      if (e.key === "a" || e.key === "A") {
        e.preventDefault();
        addToChatContext({ kind: "item", id: selected.id, label: selected.title });
        setChatOpen(true);
        return;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [items, selectedIndex, feedback, investigate, addToChatContext, setChatOpen]);

  const { containerRef, totalHeight, visibleItems, scrollToIndex } = useVirtualList<ItemCard>({
    items,
    rowHeight: ROW_HEIGHT,
  });

  useEffect(() => {
    scrollToIndex(selectedIndex);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);

  const openItemQuery = useQuery({
    queryKey: ["item", openItemId],
    queryFn: () => api.getItem(openItemId!),
    enabled: openItemId != null,
  });

  function closePanel() {
    setOpenItemId(null);
    if (searchParams.has("open")) {
      searchParams.delete("open");
      setSearchParams(searchParams, { replace: true });
    }
  }

  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="flex min-w-0 flex-1 flex-col">
        <FeedFilters value={filters} onChange={setFilters} />
        <div className="border-b border-border bg-bg-raised px-3 py-1.5 text-xs text-fg-dim">
          {data ? `${data.total ?? items.length} פריטים` : "…"} · ניווט: J/K · דרג: 1-4 · X ארכיון · Enter פרטים · I חקור · A הוסף להקשר
        </div>

        {isLoading && <LoadingState label="טוען פיד…" />}
        {isError && <ErrorState onRetry={() => refetch()} />}
        {!isLoading && !isError && items.length === 0 && (
          <EmptyState title="אין פריטים תואמים" description="נסה לשנות את מסנני החיפוש." />
        )}

        {!isLoading && !isError && items.length > 0 && (
          <div
            ref={containerRef}
            role="grid"
            aria-label="פיד Triage"
            className="relative flex-1 overflow-y-auto"
            data-testid="feed-list"
          >
            <div style={{ height: totalHeight, position: "relative" }}>
              {visibleItems.map(({ item, index, top }) => (
                <FeedRow
                  key={item.id}
                  item={item}
                  selected={index === selectedIndex}
                  onSelect={() => setSelectedIndex(index)}
                  onOpen={() => setOpenItemId(item.id)}
                  style={{ top }}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {openItemId != null && (
        <div className="h-80 w-full shrink-0 border-t border-border bg-bg-raised md:h-auto md:w-[26rem] md:border-t-0 md:border-r">
          {openItemQuery.isLoading && <LoadingState />}
          {openItemQuery.data && (
            <FeedDetailPanel item={openItemQuery.data} onClose={closePanel} />
          )}
        </div>
      )}
    </div>
  );
}
