import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemCard, ItemsResponse, TriageLevel } from "@/types/api";
import { api } from "@/api";
import { FeedFilters, type FeedFiltersState } from "@/components/feed/FeedFilters";
import { FeedRow } from "@/components/feed/FeedRow";
import { FeedDetailPanel } from "@/components/feed/FeedDetailPanel";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useVirtualList } from "@/hooks/useVirtualList";
import { useUiStore } from "@/store/uiStore";

const ROW_HEIGHT = 64;
const PAGE_SIZE = 100;

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
  const navigate = useNavigate();
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

  const {
    data,
    isLoading,
    isError,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["items", filters],
    queryFn: ({ pageParam }) =>
      api.getItems({
        level: filters.levels.length ? filters.levels : undefined,
        domain: filters.domain || undefined,
        q: filters.q || undefined,
        sort: filters.sort,
        page: pageParam,
        page_size: PAGE_SIZE,
      }),
    initialPageParam: 1,
    getNextPageParam: (lastPage: ItemsResponse, allPages: ItemsResponse[]) => {
      const fetched = allPages.reduce((n, p) => n + p.items.length, 0);
      return fetched < lastPage.total ? allPages.length + 1 : undefined;
    },
  });

  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data]);
  const total = data?.pages[0]?.total ?? items.length;

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
        setSelectedIndex((i) => {
          const next = Math.min(items.length - 1, i + 1);
          if (next >= items.length - 3 && hasNextPage && !isFetchingNextPage) {
            fetchNextPage();
          }
          return next;
        });
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
        navigate(`/items/${selected.id}`);
        return;
      }
      if (e.key === " " || e.key === "Spacebar") {
        // Quick preview (like the double-click gesture on a row): opens the
        // inline FeedDetailPanel for the selected row without navigating
        // away from the feed. Enter is reserved for the full /items/:id page.
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
      if (e.key === "o" || e.key === "O") {
        e.preventDefault();
        if (selected.url) window.open(selected.url, "_blank", "noopener,noreferrer");
        return;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    items,
    selectedIndex,
    feedback,
    investigate,
    addToChatContext,
    setChatOpen,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    navigate,
    setOpenItemId,
  ]);

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
          {data ? `מציג ${items.length} מתוך ${total}` : "…"} · ניווט: J/K · דרג: 1-4 · X ארכיון ·
          Enter פרטים · Space תצוגה מהירה · I חקור · A הוסף להקשר · O פתח מקור
        </div>

        {isLoading && <LoadingState label="טוען פיד…" />}
        {isError && <ErrorState onRetry={() => refetch()} />}
        {!isLoading && !isError && items.length === 0 && (
          <EmptyState title="אין פריטים תואמים" description="נסה לשנות את מסנני החיפוש." />
        )}

        {!isLoading && !isError && items.length > 0 && (
          <div
            ref={containerRef}
            className="relative flex-1 overflow-y-auto"
            data-testid="feed-list"
            onScroll={(e) => {
              const el = e.currentTarget;
              if (
                hasNextPage &&
                !isFetchingNextPage &&
                el.scrollTop + el.clientHeight >= el.scrollHeight - 400
              ) {
                fetchNextPage();
              }
            }}
          >
            {/* role="list" lives on this inner wrapper (not the scroll
                container above) so its only children are the FeedRow
                listitems — the "טען עוד" button below is a sibling, not a
                list child, which axe's aria-required-children rule forbids. */}
            <div role="list" aria-label="פיד Triage" style={{ height: totalHeight, position: "relative" }}>
              {visibleItems.map(({ item, index, top }) => (
                <FeedRow
                  key={item.id}
                  item={item}
                  selected={index === selectedIndex}
                  onSelect={() => setSelectedIndex(index)}
                  onOpen={() => setOpenItemId(item.id)}
                  onRate={(level) => feedback.mutate({ id: item.id, level })}
                  isRating={feedback.isPending}
                  style={{ top }}
                />
              ))}
            </div>
            {hasNextPage && (
              <div className="flex justify-center border-t border-border py-3">
                <button
                  type="button"
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-xs text-fg-dim hover:bg-bg-sunken disabled:opacity-50"
                >
                  {isFetchingNextPage ? "טוען…" : `טען עוד (${total - items.length} נותרו)`}
                </button>
              </div>
            )}
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
