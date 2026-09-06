import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HelpCircle, Map as MapIcon } from "lucide-react";
import type { ItemCard, ItemsResponse, TriageLevel } from "@/types/api";
import { api, ApiError } from "@/api";
import { FeedFilters, type FeedFiltersState } from "@/components/feed/FeedFilters";
import { FeedRow } from "@/components/feed/FeedRow";
import { FeedDetailPanel } from "@/components/feed/FeedDetailPanel";
import { ShortcutsDialog } from "@/components/feed/ShortcutsDialog";
import { CountryMapPanel } from "@/components/feed/CountryMapPanel";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ToastStack } from "@/components/ToastStack";
import { useToastQueue } from "@/hooks/useToastQueue";
import { useVirtualList } from "@/hooks/useVirtualList";
import { useUiStore } from "@/store/uiStore";
import { useI18n, useT } from "@/i18n";
import { countryOption, normalizeCountryCode } from "@/lib/countries";
import { cn } from "@/lib/cn";

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
    countries: [],
    groupByCountry: false,
  });
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [openItemId, setOpenItemId] = useState<number | null>(null);
  // U2 (docs/REVIEW_2026-09-05.md): the Morning "פריטים שנקלטו" KPI card deep-links here as
  // `?since=24h` — kept as a separate ISO cutoff (not part of `FeedFiltersState`, which mirrors
  // the visible filter bar) so it doesn't need a UI control of its own.
  const [sinceFilter, setSinceFilter] = useState<string | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [countryPanelOpen, setCountryPanelOpen] = useState(false);
  const addToChatContext = useUiStore((s) => s.addToChatContext);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const queryClient = useQueryClient();
  const t = useT();
  const { locale } = useI18n();

  const {
    data,
    isLoading,
    isError,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["items", filters, sinceFilter],
    queryFn: ({ pageParam }) =>
      api.getItems({
        level: filters.levels.length ? filters.levels : undefined,
        domain: filters.domain || undefined,
        q: filters.q || undefined,
        since: sinceFilter ?? undefined,
        country: filters.countries.length ? filters.countries : undefined,
        group_by: filters.groupByCountry ? "country" : undefined,
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

  // U7b: grouped display order -- a stable partition by normalized country
  // (preserving each item's relative order within its group), covering
  // whatever pages are currently loaded. Keyboard nav (J/K), selection and
  // scrolling all operate on this order, so "next"/"previous" always matches
  // what's on screen, grouped or not.
  const orderedItems = useMemo(() => {
    if (!filters.groupByCountry) return items;
    const groups = new Map<string, ItemCard[]>();
    for (const it of items) {
      const code = normalizeCountryCode(it.geography);
      const bucket = groups.get(code);
      if (bucket) bucket.push(it);
      else groups.set(code, [it]);
    }
    return [...groups.values()].flat();
  }, [items, filters.groupByCountry]);

  const groupHeaderAt = useMemo(() => {
    const headers = new Map<number, { code: string; count: number }>();
    if (!filters.groupByCountry) return headers;
    const counts = new Map<string, number>();
    for (const it of orderedItems) {
      const code = normalizeCountryCode(it.geography);
      counts.set(code, (counts.get(code) ?? 0) + 1);
    }
    let lastCode: string | null = null;
    orderedItems.forEach((it, idx) => {
      const code = normalizeCountryCode(it.geography);
      if (code !== lastCode) {
        headers.set(idx, { code, count: counts.get(code) ?? 0 });
        lastCode = code;
      }
    });
    return headers;
  }, [orderedItems, filters.groupByCountry]);

  useEffect(() => {
    const openParam = searchParams.get("open");
    if (openParam) {
      setOpenItemId(Number(openParam));
    }
  }, [searchParams]);

  // U2: apply the Morning KPI cards' deep-link filters (`?level=red`, `?since=24h`) once, on
  // arrival — the URL is the trigger, not a permanently-bound control, so later changes made via
  // the visible filter bar aren't clobbered by this effect re-running (it only reacts to the
  // search params themselves changing, which a filter-bar edit doesn't touch).
  useEffect(() => {
    const levelParam = searchParams.get("level") as TriageLevel | null;
    const sinceParam = searchParams.get("since");
    if (levelParam) {
      setFilters((f) => ({ ...f, levels: [levelParam] }));
    }
    if (sinceParam === "24h") {
      setSinceFilter(new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString());
    }
  }, [searchParams]);

  useEffect(() => {
    if (selectedIndex >= orderedItems.length) setSelectedIndex(Math.max(0, orderedItems.length - 1));
  }, [orderedItems, selectedIndex]);

  const feedback = useMutation({
    mutationFn: ({ id, level }: { id: number; level: TriageLevel }) =>
      api.postItemFeedback(id, { user_level: level, comment: null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
  });

  // Q5-3 (docs/qa/findings_Q5_r1.md): the "I" shortcut used to POST with no feedback at all, allow
  // double-submits (two clicks/presses queued two overlapping jobs), and never check whether an
  // investigation already existed for the item. `pendingInvestigateIds` debounces per item while a
  // request is in flight; `activeInvestigationItemIds` (polled from `/api/investigations`) also
  // covers a job started elsewhere (e.g. from /items/:id) and drives the row's "🔎 בחקירה" badge.
  const [pendingInvestigateIds, setPendingInvestigateIds] = useState<Set<number>>(new Set());
  const { toasts, push: pushToast, dismiss: dismissToast } = useToastQueue();

  const activeInvestigationsQuery = useQuery({
    queryKey: ["investigations", "feed-active"],
    queryFn: () => api.getInvestigations(100),
    refetchInterval: 6000,
  });
  const activeInvestigationItemIds = useMemo(() => {
    const ids = new Set<number>();
    for (const inv of activeInvestigationsQuery.data ?? []) {
      if ((inv.state === "queued" || inv.state === "running") && inv.item_id != null) {
        ids.add(inv.item_id);
      }
    }
    return ids;
  }, [activeInvestigationsQuery.data]);

  const investigate = useMutation({
    mutationFn: (id: number) => api.postItemInvestigate(id, { question: null }),
    onMutate: (id: number) => {
      setPendingInvestigateIds((prev) => new Set(prev).add(id));
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["investigations"] });
      const toastKey = res.existing ? "feed.investigateExistingToast" : "feed.investigateQueuedToast";
      pushToast(t(toastKey, { jobId: res.job_id }), {
        tone: res.existing ? "info" : "ok",
        linkTo: `/investigations/${res.job_id}`,
        linkLabel: t("feed.investigateToastViewLink"),
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.code === "conflict") {
        const detail = err.detail as { job_id?: number } | null;
        pushToast(t("feed.investigateConflictToast"), {
          tone: "warn",
          linkTo: detail?.job_id != null ? `/investigations/${detail.job_id}` : undefined,
          linkLabel: t("feed.investigateToastViewLink"),
        });
      } else {
        pushToast(t("feed.investigateErrorToast"), { tone: "danger" });
      }
    },
    onSettled: (_res, _err, id) => {
      setPendingInvestigateIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
  });

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(document.activeElement)) return;
      const selected = orderedItems[selectedIndex];

      if (e.key === "j" || e.key === "J" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => {
          const next = Math.min(orderedItems.length - 1, i + 1);
          if (next >= orderedItems.length - 3 && hasNextPage && !isFetchingNextPage) {
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
        // Q5-3: skip if this item already has a request in flight (client-side debounce) or is
        // already known to be queued/running server-side -- avoids a redundant 409 round-trip.
        if (pendingInvestigateIds.has(selected.id) || activeInvestigationItemIds.has(selected.id)) {
          return;
        }
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
    orderedItems,
    selectedIndex,
    feedback,
    investigate,
    pendingInvestigateIds,
    activeInvestigationItemIds,
    addToChatContext,
    setChatOpen,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    navigate,
    setOpenItemId,
  ]);

  const { containerRef, totalHeight, visibleItems, scrollToIndex } = useVirtualList<ItemCard>({
    items: orderedItems,
    rowHeight: ROW_HEIGHT,
  });

  useEffect(() => {
    if (filters.groupByCountry) {
      containerRef.current
        ?.querySelector(`[data-row-index="${selectedIndex}"]`)
        ?.scrollIntoView({ block: "nearest" });
    } else {
      scrollToIndex(selectedIndex);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex, filters.groupByCountry]);

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

        <div className="flex items-center justify-between gap-2 border-b border-border bg-bg-raised px-3 py-1.5 text-xs text-fg-dim">
          <span>{data ? t("feed.showingStatus", { shown: items.length, total }) : "…"}</span>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setCountryPanelOpen((v) => !v)}
              aria-pressed={countryPanelOpen}
              aria-label={t("feed.countryPanelTitle")}
              title={t("feed.countryPanelTitle")}
              data-testid="country-map-toggle"
              className={cn(
                "flex items-center gap-1 rounded-md border px-2 py-1 text-xs",
                countryPanelOpen
                  ? "border-accent text-accent"
                  : "border-border-strong text-fg-dim hover:bg-bg-sunken",
              )}
            >
              <MapIcon size={12} aria-hidden="true" />
              <span className="hidden sm:inline">{t("feed.countryPanelTitle")}</span>
            </button>
            <button
              type="button"
              onClick={() => setShortcutsOpen(true)}
              data-testid="shortcuts-button"
              aria-label={t("feed.shortcutsButtonLabel")}
              title={t("feed.shortcutsButtonLabel")}
              className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-dim hover:bg-bg-sunken"
            >
              <HelpCircle size={12} aria-hidden="true" />
              <span className="hidden sm:inline">{t("feed.shortcutsButtonLabel")}</span>
            </button>
          </div>
        </div>

        {countryPanelOpen && (
          <CountryMapPanel
            levels={filters.levels}
            domain={filters.domain}
            selected={filters.countries}
            onToggle={(code) =>
              setFilters((f) => ({
                ...f,
                countries: f.countries.includes(code)
                  ? f.countries.filter((c) => c !== code)
                  : [...f.countries, code],
              }))
            }
            onClose={() => setCountryPanelOpen(false)}
          />
        )}

        {isLoading && <LoadingState label={t("feed.loading")} />}
        {isError && <ErrorState onRetry={() => refetch()} />}
        {!isLoading && !isError && items.length === 0 && (
          <EmptyState title={t("feed.emptyTitle")} description={t("feed.emptyDescription")} />
        )}

        {!isLoading && !isError && items.length > 0 && !filters.groupByCountry && (
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
                  investigating={pendingInvestigateIds.has(item.id) || activeInvestigationItemIds.has(item.id)}
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
                  {isFetchingNextPage ? t("feed.loadingMore") : t("feed.loadMore", { remaining: total - items.length })}
                </button>
              </div>
            )}
          </div>
        )}

        {/* U7b: grouped-by-country view — not windowed/virtualized (unlike
            the flat feed above), since headers of varying position break the
            fixed-row-height virtualizer; acceptable for the currently-loaded
            page(s), which this toggle is scoped to. */}
        {!isLoading && !isError && items.length > 0 && filters.groupByCountry && (
          <div
            ref={containerRef}
            className="relative flex-1 overflow-y-auto"
            data-testid="feed-list-grouped"
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
            <div role="list" aria-label="פיד Triage">
              {orderedItems.map((item, index) => {
                const header = groupHeaderAt.get(index);
                return (
                  <div key={item.id}>
                    {header && (
                      <div
                        role="presentation"
                        data-testid={`country-group-header-${header.code}`}
                        className="flex items-center gap-2 border-b border-t border-border bg-bg-sunken px-3 py-1.5 text-xs font-medium text-fg-muted"
                      >
                        <span aria-hidden="true">{countryOption(header.code).flag}</span>
                        <bdi>{locale === "he" ? countryOption(header.code).nameHe : countryOption(header.code).nameEn}</bdi>
                        <span className="font-mono font-tabular text-fg-dim">
                          {t("feed.countryGroupCount", { count: header.count })}
                        </span>
                      </div>
                    )}
                    <div data-row-index={index} style={{ position: "relative", height: ROW_HEIGHT }}>
                      <FeedRow
                        item={item}
                        selected={index === selectedIndex}
                        onSelect={() => setSelectedIndex(index)}
                        onOpen={() => setOpenItemId(item.id)}
                        onRate={(level) => feedback.mutate({ id: item.id, level })}
                        isRating={feedback.isPending}
                        investigating={pendingInvestigateIds.has(item.id) || activeInvestigationItemIds.has(item.id)}
                        style={{ top: 0 }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
            {hasNextPage && (
              <div className="flex justify-center border-t border-border py-3">
                <button
                  type="button"
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-xs text-fg-dim hover:bg-bg-sunken disabled:opacity-50"
                >
                  {isFetchingNextPage ? t("feed.loadingMore") : t("feed.loadMore", { remaining: total - items.length })}
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

      {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
