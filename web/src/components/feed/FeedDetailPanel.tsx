import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, HelpCircle, Maximize2, Search, X } from "lucide-react";
import type { ItemCard, TriageLevel } from "@/types/api";
import { LevelBadge } from "@/components/LevelBadge";
import { AddToContextButton } from "@/components/AddToContextButton";
import { domainLabel } from "@/lib/taxonomy";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { api } from "@/api";

const LEVEL_KEYS: Record<string, TriageLevel> = {
  "1": "red",
  "2": "orange",
  "3": "yellow",
  "4": "archive",
};

export function FeedDetailPanel({
  item,
  onClose,
}: {
  item: ItemCard;
  onClose: () => void;
}) {
  const [showWhy, setShowWhy] = useState(false);
  const queryClient = useQueryClient();
  const keyFacts = item.key_facts ?? [];
  const entitiesMentioned = item.entities_mentioned ?? [];
  // Real data (2026-09-04 QA against the live backend): a handful of
  // ingested items carry an empty `title` — show a placeholder instead of a
  // blank heading.
  const displayTitle = item.title || "(ללא כותרת)";

  const feedback = useMutation({
    mutationFn: (level: TriageLevel) => api.postItemFeedback(item.id, { user_level: level, comment: null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });

  const investigate = useMutation({
    mutationFn: () => api.postItemInvestigate(item.id, { question: null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["investigations"] });
    },
  });

  return (
    <div className="flex h-full flex-col" data-testid="feed-detail-panel">
      <div className="flex items-start gap-2 border-b border-border p-3">
        <LevelBadge level={item.level} />
        <div className="min-w-0 flex-1">
          <bdi className={cn("block font-semibold", item.title ? "text-fg" : "italic text-fg-dim")}>
            {displayTitle}
          </bdi>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-dim">
            <bdi>{item.source_name}</bdi>
            <span>·</span>
            <span className="font-mono">{formatDateTime(item.published_at)}</span>
            {/* W8: an explicit domain chip (mirroring FeedRow's own pill), not bare text --
                paired with the LevelBadge chip above so both classification facets read as
                chips in the drawer, same as the rest of the app's triage conventions. */}
            <span className="rounded-full bg-bg-sunken px-2 py-0.5 text-fg-muted">
              {domainLabel(item.domain)}
            </span>
          </div>
        </div>
        <Link
          to={`/items/${item.id}`}
          className="rounded p-1.5 text-fg-muted hover:bg-bg-sunken"
          aria-label="פתח עמוד מלא"
          title="פתח עמוד מלא"
        >
          <Maximize2 size={16} aria-hidden="true" />
        </Link>
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="rounded p-1.5 text-fg-muted hover:bg-bg-sunken"
          aria-label="פתח מקור מקורי"
        >
          <ExternalLink size={16} aria-hidden="true" />
        </a>
        <button
          type="button"
          onClick={onClose}
          className="tap-target inline-flex items-center justify-center rounded p-1.5 text-fg-muted hover:bg-bg-sunken"
          aria-label="סגור פרטים"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setShowWhy((v) => !v)}
            className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
          >
            <HelpCircle size={12} aria-hidden="true" />
            למה הציון?
          </button>
          <AddToContextButton kind="item" id={item.id} label={displayTitle} size="sm" />
          <button
            type="button"
            onClick={() => investigate.mutate()}
            disabled={investigate.isPending}
            className="flex items-center gap-1 rounded-md border border-hot px-2 py-1 text-xs text-hot hover:bg-hot/10 disabled:opacity-50"
          >
            <Search size={12} aria-hidden="true" />
            {investigate.isPending ? "פותח חקירה…" : "חקור לעומק (I)"}
          </button>
        </div>

        {showWhy && (
          <p className="rounded-md border border-border-strong bg-bg-sunken p-2 text-xs text-fg-muted">
            {item.triage_reason ?? "אין נימוק זמין."}
          </p>
        )}

        {item.summary_he && (
          <section>
            <h3 className="mb-1 text-xs font-semibold text-fg-dim">תקציר</h3>
            <bdi className="block text-sm leading-relaxed text-fg">{item.summary_he}</bdi>
          </section>
        )}

        {item.so_what_he && (
          <section>
            <h3 className="mb-1 text-xs font-semibold text-fg-dim">אז מה?</h3>
            <bdi className="block text-sm leading-relaxed text-fg">{item.so_what_he}</bdi>
          </section>
        )}

        {keyFacts.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-semibold text-fg-dim">עובדות מפתח</h3>
            <ul className="list-disc space-y-1 ps-4 text-sm text-fg">
              {keyFacts.map((f, i) => (
                <li key={i}>
                  <bdi>{f}</bdi>
                </li>
              ))}
            </ul>
          </section>
        )}

        {entitiesMentioned.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-semibold text-fg-dim">ישויות מוזכרות</h3>
            <div className="flex flex-wrap gap-1.5">
              {entitiesMentioned.map((e) => (
                <span
                  key={e}
                  className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted"
                >
                  <bdi>{e}</bdi>
                </span>
              ))}
            </div>
          </section>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-border p-3">
        <span className="text-xs text-fg-dim">קביעת רמה:</span>
        {(Object.entries(LEVEL_KEYS) as [string, TriageLevel][]).map(([key, level]) => (
          <button
            key={level}
            type="button"
            onClick={() => feedback.mutate(level)}
            disabled={feedback.isPending}
            className="rounded-md border border-border-strong px-2 py-1 text-xs hover:bg-bg-sunken disabled:opacity-50"
            title={`${key} — קבע כ-${level}`}
          >
            {key}
          </button>
        ))}
      </div>
    </div>
  );
}
