import { ContentShareActions } from "@/components/ContentShareActions";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Search } from "lucide-react";
import type { ItemDetail } from "@/types/api";
import { api } from "@/api";
import { LevelBadge } from "@/components/LevelBadge";
import { AddToContextButton } from "@/components/AddToContextButton";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ExplainScorePopover } from "@/components/feed/ExplainScorePopover";
import { SecurityStatusIcon } from "@/components/feed/SecurityStatusIcon";
import { CorroborationBadge } from "@/components/feed/CorroborationBadge";
import { DuplicateOutletsPopover } from "@/components/feed/DuplicateOutletsPopover";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ToastStack } from "@/components/ToastStack";
import { useToastQueue } from "@/hooks/useToastQueue";
import { domainLabel } from "@/lib/taxonomy";
import { formatDateTime } from "@/lib/time";
import { outcomeLabel, outcomeTone } from "@/lib/investigations";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";

const INV_STATE_LABEL: Record<string, string> = {
  queued: "בתור",
  running: "רץ",
  done: "הושלם",
  stopped: "נעצר",
  error: "שגיאה",
  not_found: "לא נמצא",
};

export function ItemDetailPage() {
  const { id } = useParams<{ id: string }>();
  const itemId = Number(id);
  const queryClient = useQueryClient();
  const t = useT();
  const { toasts, push: pushToast, dismiss: dismissToast } = useToastQueue();
  // Round-3 mobile fix (UI-MOBILE-iphone-r3.md #3): "חקור לעומק" kicks off a several-minute
  // cloud-model deep search -- confirm before firing, so a stray phone tap doesn't start one.
  const [confirmInvestigateOpen, setConfirmInvestigateOpen] = useState(false);

  const itemQuery = useQuery({
    queryKey: ["item", itemId],
    queryFn: () => api.getItem(itemId),
    enabled: !Number.isNaN(itemId),
  });

  // R10-links: richer than `item.investigations` (job_id/question/state/dates only) -- adds
  // outcome/confidence/lineage pointers for the badge this block shows below.
  const itemInvestigationsQuery = useQuery({
    queryKey: ["item-investigations", itemId],
    queryFn: () => api.getItemInvestigations(itemId),
    enabled: !Number.isNaN(itemId),
  });

  // Resolve entities_mentioned (bare names on the item) and edge src/dst
  // (entity ids) against the tracked entity roster, so both can link out to
  // /entities/:id. Not every mentioned name is a tracked entity (person
  // names, generic terms) — those fall back to a plain, non-linking chip.
  const entitiesQuery = useQuery({
    queryKey: ["entities", "__all_for_linking"],
    queryFn: () => api.getEntities({ limit: 200 }),
    staleTime: 5 * 60_000,
  });

  const { nameToId, idToName } = useMemo(() => {
    const nameToId = new Map<string, number>();
    const idToName = new Map<number, string>();
    for (const e of entitiesQuery.data ?? []) {
      nameToId.set(e.name.toLowerCase(), e.id);
      idToName.set(e.id, e.name);
    }
    return { nameToId, idToName };
  }, [entitiesQuery.data]);

  const feedback = useMutation({
    mutationFn: (level: Parameters<typeof api.postItemFeedback>[1]["user_level"]) =>
      api.postItemFeedback(itemId, { user_level: level, comment: null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["item", itemId] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });

  const investigate = useMutation({
    mutationFn: () => api.postItemInvestigate(itemId, { question: null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["investigations"] }),
  });

  // CORR (cross-source corroboration, 2026-09-07): "בדוק אימות מחדש" -- see FeedDetailPanel's
  // twin mutation for the inline-drawer variant of this same action.
  const recheckCorroboration = useMutation({
    mutationFn: () => api.postItemCorroborate(itemId),
    onSuccess: (corroboration) => {
      queryClient.setQueryData<ItemDetail | undefined>(["item", itemId], (old) =>
        old ? { ...old, corroboration } : old,
      );
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
    onError: () => {
      pushToast(t("corr.recheckErrorToast"), { tone: "danger" });
    },
  });

  if (Number.isNaN(itemId)) return <ErrorState message="מזהה פריט לא תקין" />;
  if (itemQuery.isLoading) return <LoadingState label="טוען פריט…" />;
  if (itemQuery.isError || !itemQuery.data) {
    return <ErrorState onRetry={() => itemQuery.refetch()} message="הפריט לא נמצא" />;
  }

  const item = itemQuery.data;
  const hasUrl = Boolean(item.url);
  // Real data (2026-09-04 QA against the live backend): a handful of
  // ingested items carry an empty `title` (a fetch/parse gap upstream, out
  // of scope here) — show a visible placeholder instead of a blank heading.
  const displayTitle = item.title || "(ללא כותרת)";

  return (
    <div data-share-content className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-start gap-2">
          <LevelBadge level={item.level} />
          <CorroborationBadge corroboration={item.corroboration} showUnknown />
          {/* Story clustering (2026-09-17): "אותה ידיעה במקורות נוספים" -- same popover as the
              feed row's "+N מקורות" chip. */}
          {(item.story_members?.length ?? 0) > 0 && (
            <DuplicateOutletsPopover duplicates={item.story_members ?? []} size="md" />
          )}
          {/* Content review (docs/qa/content_review/CR-ui.md): with `min-w-0`, this flex item
              had no minimum size to defend, so on a narrow viewport where the two badges above
              already ate most of the row's width, `flex-1` shrank the title down to whatever was
              left (confirmed live: 85px of a 269px row) instead of the wrapping row moving the
              title to its own full-width line -- flex-wrap only wraps an item that *can't* fit
              its minimum, and `min-w-0` means anything fits. The title isn't truncated (it wraps
              to multiple lines, no `truncate`/`line-clamp` here), so there's no reason for
              `min-w-0` in the first place -- a real minimum lets the row wrap the title down to
              its own line once the badges leave it under ~12rem, instead of rendering it as a
              near-unreadable single-word-per-line column. */}
          <div className="min-w-[12rem] flex-1">
            {hasUrl ? (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  "inline-flex items-start gap-1.5 text-lg font-semibold hover:text-accent hover:underline",
                  item.title ? "text-fg" : "italic text-fg-dim",
                )}
              >
                <bdi>{displayTitle}</bdi>
                <ExternalLink size={14} className="mt-1.5 shrink-0" aria-hidden="true" />
              </a>
            ) : (
              <bdi
                className={cn(
                  "block text-lg font-semibold",
                  item.title ? "text-fg" : "italic text-fg-dim",
                )}
              >
                {displayTitle}
              </bdi>
            )}
          </div>
          <SecurityStatusIcon status={item.security_status} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-fg-dim">
          <bdi>{item.source_name}</bdi>
          <span>·</span>
          <span className="font-mono">{formatDateTime(item.published_at)}</span>
          <span>·</span>
          <span className="uppercase">{item.lang}</span>
          <span>·</span>
          <span>{domainLabel(item.domain)}</span>
          <span>·</span>
          <span className="font-mono">ציון: {item.score}</span>
          <ExplainScorePopover item={item} onRate={(l) => feedback.mutate(l)} isRating={feedback.isPending} size="sm" />
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <AddToContextButton kind="item" id={item.id} label={displayTitle} size="sm" />
          <button
            type="button"
            onClick={() => setConfirmInvestigateOpen(true)}
            disabled={investigate.isPending}
            className="flex items-center gap-1 rounded-md border border-hot px-2 py-1 text-xs text-hot hover:bg-hot/10 disabled:opacity-50"
          >
            <Search size={12} aria-hidden="true" />
            {investigate.isPending ? "פותח חקירה…" : "חקור לעומק"}
          </button>
          {confirmInvestigateOpen && (
            <ConfirmDialog
              title={t("feed.investigateConfirmTitle")}
              message={t("feed.investigateConfirmBody")}
              confirming={investigate.isPending}
              onConfirm={() => {
                investigate.mutate();
                setConfirmInvestigateOpen(false);
              }}
              onCancel={() => setConfirmInvestigateOpen(false)}
            />
          )}
          <button
            type="button"
            onClick={() => recheckCorroboration.mutate()}
            disabled={recheckCorroboration.isPending}
            data-testid="corroboration-recheck-button"
            className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken disabled:opacity-50"
          >
            <RefreshCw
              size={12}
              aria-hidden="true"
              className={cn(recheckCorroboration.isPending && "animate-spin")}
            />
            {recheckCorroboration.isPending ? t("corr.recheckPending") : t("corr.recheckButton")}
          </button>
        </div>
      </header>
      <ContentShareActions title={displayTitle} links={[{ url: item.url, title: displayTitle }]} />

      <section aria-label="תקציר">
        <h3 className="mb-1 text-xs font-semibold text-fg-dim">תקציר</h3>
        {item.summary_he ? (
          <bdi className="block text-sm leading-relaxed text-fg" dir="auto">
            {item.summary_he}
          </bdi>
        ) : (
          <p className="rounded-md border border-dashed border-border-strong bg-bg-sunken p-2 text-sm text-fg-dim">
            טרם סוכם — יופק בריצה הלילית
          </p>
        )}
      </section>

      <section aria-label="אז מה?">
        <h3 className="mb-1 text-xs font-semibold text-fg-dim">אז מה?</h3>
        {item.so_what_he ? (
          <bdi className="block text-sm leading-relaxed text-fg" dir="auto">
            {item.so_what_he}
          </bdi>
        ) : (
          <p className="rounded-md border border-dashed border-border-strong bg-bg-sunken p-2 text-sm text-fg-dim">
            טרם סוכם — יופק בריצה הלילית
          </p>
        )}
      </section>

      {item.key_facts.length > 0 && (
        <section aria-label="עובדות מפתח">
          <h3 className="mb-1 text-xs font-semibold text-fg-dim">עובדות מפתח</h3>
          <ul className="list-disc space-y-1 ps-4 text-sm text-fg">
            {item.key_facts.map((f, i) => (
              <li key={i}>
                <bdi dir="auto">{f}</bdi>
              </li>
            ))}
          </ul>
        </section>
      )}

      {item.uncertainty_he && (
        <section aria-label="אי-ודאות" className="rounded-md border border-warn/30 bg-warn/10 p-2">
          <h3 className="mb-1 text-xs font-semibold text-warn">אי-ודאות</h3>
          <bdi className="block text-sm text-fg" dir="auto">
            {item.uncertainty_he}
          </bdi>
        </section>
      )}

      {item.entities_mentioned.length > 0 && (
        <section aria-label="ישויות מוזכרות">
          <h3 className="mb-1 text-xs font-semibold text-fg-dim">ישויות מוזכרות</h3>
          <div className="flex flex-wrap gap-1.5">
            {item.entities_mentioned.map((name) => {
              const entityId = nameToId.get(name.toLowerCase());
              return entityId != null ? (
                <Link
                  key={name}
                  to={`/entities/${entityId}`}
                  className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-accent hover:underline"
                >
                  <bdi>{name}</bdi>
                </Link>
              ) : (
                <span key={name} className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted">
                  <bdi>{name}</bdi>
                </span>
              );
            })}
          </div>
        </section>
      )}

      {item.edges.length > 0 && (
        <section aria-label="קשרים בגרף">
          <h3 className="mb-1 text-xs font-semibold text-fg-dim">קשרים בגרף שהפריט מעיד עליהם</h3>
          <ul className="space-y-1.5 text-sm">
            {item.edges.map((e, i) => (
              <li key={i} className="rounded-md border border-border bg-bg-raised p-2 text-xs">
                <div className="flex flex-wrap items-center gap-1">
                  <bdi className="font-medium text-fg">{idToName.get(e.src) ?? `#${e.src}`}</bdi>
                  <span className="rounded bg-accent-muted px-1.5 py-0.5 text-accent-fg">
                    {e.label}
                  </span>
                  <bdi className="font-medium text-fg">{idToName.get(e.dst) ?? `#${e.dst}`}</bdi>
                </div>
                {e.evidence && (
                  <bdi className="mt-1 block text-fg-dim" dir="auto">
                    {e.evidence}
                  </bdi>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {(itemInvestigationsQuery.data ?? item.investigations).length > 0 && (
        <section aria-label="חקירות עומק לפריט">
          <h3 className="mb-1 text-xs font-semibold text-fg-dim">חקירות עומק</h3>
          <ul className="space-y-1.5 text-sm">
            {/* R10-links: prefer the richer `itemInvestigationsQuery` (outcome/confidence/date) --
                falls back to the plain `item.investigations` list while that query is loading or
                on an older backend that doesn't have the endpoint yet. */}
            {(itemInvestigationsQuery.data ?? item.investigations).map((inv) => (
              <li
                key={inv.job_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-bg-raised p-2 text-xs"
              >
                <Link to={`/investigations/${inv.job_id}`} className="min-w-0 flex-1 text-accent hover:underline">
                  <bdi className="block truncate" title={inv.question || `חקירה #${inv.job_id}`}>
                    {inv.question || `חקירה #${inv.job_id}`}
                  </bdi>
                </Link>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-fg-dim">
                    {INV_STATE_LABEL[inv.state] ?? inv.state}
                  </span>
                  {"outcome" in inv && inv.outcome && (
                    <span className={cn("rounded-full px-1.5 py-0.5 font-medium", outcomeTone(inv.outcome))}>
                      {outcomeLabel(inv.outcome)}
                    </span>
                  )}
                  {"confidence" in inv && typeof inv.confidence === "number" && (
                    <span className="font-mono text-fg-dim">{inv.confidence.toFixed(2)}</span>
                  )}
                  {"finished_at" in inv && inv.finished_at && (
                    <span className="font-mono text-fg-dim">{formatDateTime(inv.finished_at)}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {item.clean_text && (
        <details className="rounded-md border border-border">
          <summary className="cursor-pointer select-none p-2 text-xs font-semibold text-fg-dim hover:bg-bg-sunken">
            טקסט מלא (מקור)
          </summary>
          <div className="max-h-96 overflow-y-auto border-t border-border p-3">
            <bdi className="block whitespace-pre-wrap text-sm leading-relaxed text-fg-muted" dir="auto">
              {item.clean_text}
            </bdi>
          </div>
        </details>
      )}

      {!item.summary_he &&
        !item.so_what_he &&
        item.key_facts.length === 0 &&
        !item.clean_text && (
          <EmptyState
            title="עדיין אין ניתוח לפריט זה"
            description="הפריט נקלט אך טרם עבר את שלב הסיווג/הסיכום של הריצה הלילית."
          />
        )}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
