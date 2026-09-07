import { Link, useNavigate } from "react-router-dom";
import { ExternalLink, Search } from "lucide-react";
import { useMutation, useQueries } from "@tanstack/react-query";
import { api } from "@/api";
import type { ForecastCard, ItemDetail } from "@/types/api";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import {
  LIKELIHOOD_BAND_BAR_CLASS,
  LIKELIHOOD_BAND_CHIP_CLASS,
  likelihoodBand,
} from "@/lib/tenders";
import { EmptyState } from "@/components/states";
import { SourcePreviewPopover } from "@/components/SourcePreviewPopover";

const RATIONALE_ITEM_RE = /(\[item \d+\])/g;
const RATIONALE_ITEM_MATCH_RE = /^\[item (\d+)\]$/;

// W22 (docs/REVIEW_2026-09-06_evening.md round 4b): `tender_forecasts.sources` stores
// `["item:123", ...]` tokens (agent/eoa/tenders/forecast.py's `_upsert_forecast`), not the plain
// URLs the old "מקור 1/2…" chip assumed -- each chip's `href` was literally the string "item:123",
// a dead link to nowhere. A source is resolved to the item it names (real URL, outlet name, date)
// via `api.getItem`; anything that isn't an `item:N` token (an older/legacy row, or a real URL
// already) is rendered as a direct link instead.
const ITEM_SOURCE_RE = /^item:(\d+)$/;

interface ResolvedSource {
  key: string;
  itemId: number | null;
  url: string | null;
  label: string;
  title: string | undefined;
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Resolves every `f.sources` entry to a real, labeled link -- `item:N` tokens via `api.getItem`
 * (cached/deduplicated across cards by react-query), anything else rendered as-is. */
function useResolvedSources(sources: string[]): ResolvedSource[] {
  const itemIds = Array.from(
    new Set(
      sources
        .map((s) => s.match(ITEM_SOURCE_RE)?.[1])
        .filter((v): v is string => v !== undefined)
        .map(Number),
    ),
  );
  const results = useQueries({
    queries: itemIds.map((id) => ({
      queryKey: ["item", id],
      queryFn: () => api.getItem(id),
      staleTime: 5 * 60_000,
    })),
  });
  const byId = new Map<number, ItemDetail>();
  itemIds.forEach((id, i) => {
    const data = results[i]?.data;
    if (data) byId.set(id, data);
  });
  return sources.map((s) => {
    const itemIdStr = s.match(ITEM_SOURCE_RE)?.[1];
    if (itemIdStr !== undefined) {
      const item = byId.get(Number(itemIdStr));
      return {
        key: s,
        itemId: Number(itemIdStr),
        url: item?.url ?? null,
        label: item ? `${item.source_name} · ${formatDate(item.published_at)}` : `פריט ${itemIdStr}`,
        title: item?.title,
      };
    }
    return { key: s, itemId: null, url: s, label: domainOf(s), title: s };
  });
}

/** Renders `rationale_he`, turning every `[item N]` token into a link to /items/N. */
function ForecastRationale({ text }: { text: string | null }) {
  if (!text) return <p className="text-sm text-fg-dim">אין נימוק זמין</p>;
  const parts = text.split(RATIONALE_ITEM_RE);
  return (
    <bdi className="block text-sm leading-relaxed text-fg" dir="auto">
      {parts.map((part, i) => {
        const match = part.match(RATIONALE_ITEM_MATCH_RE);
        if (!match) return <span key={i}>{part}</span>;
        const itemId = Number(match[1]);
        return (
          <Link
            key={i}
            to={`/items/${itemId}`}
            className="mx-0.5 inline-flex rounded bg-accent-muted px-1 py-0.5 font-mono text-xs font-semibold text-accent-fg hover:bg-accent"
            dir="ltr"
          >
            {part}
          </Link>
        );
      })}
    </bdi>
  );
}

function LikelihoodMeter({ value }: { value: number | null }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  const pct = Math.round(v * 100);
  const band = likelihoodBand(value);
  return (
    <div className="flex items-center gap-2" title={`סבירות: ${pct}%`}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg-sunken" dir="ltr">
        <div
          className={cn("h-full rounded-full", LIKELIHOOD_BAND_BAR_CLASS[band])}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span
        className={cn("shrink-0 rounded px-1.5 py-0.5 font-mono text-xs", LIKELIHOOD_BAND_CHIP_CLASS[band])}
      >
        {pct}%
      </span>
    </div>
  );
}

/** The forecast's own research question, prefilled into a new deep-search investigation --
 * self-contained (no reference to "this forecast") since the investigation carries no link back
 * to it. */
function forecastInvestigationQuestion(f: ForecastCard): string {
  const buyer = f.buyer_country ? ` ב-${f.buyer_country}` : "";
  const window =
    f.window_from && f.window_to ? ` בחלון ${formatDate(f.window_from)}–${formatDate(f.window_to)}` : "";
  return (
    `מה ההתקדמות בפועל בתחזית הרכש עבור ${f.platform || "הפלטפורמה"} ` +
    `(${f.payload_need || "הצורך שזוהה"})${buyer}${window}? בדוק מכרזים/RFI/RFP חדשים, אירועי חוזה ` +
    "או פריסה, והאם התחזית עדיין תקפה נכון להיום."
  );
}

/** W22: the "חפירה" (deep-dive) affordance -- states plainly what it does (title/aria-label) and
 * opens a new deep-search investigation with the forecast's own question prefilled, instead of an
 * unlabeled icon whose destination was unclear. */
function DeepDiveButton({ f }: { f: ForecastCard }) {
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: () => api.postInvestigationNew({ question: forecastInvestigationQuestion(f) }),
    onSuccess: (res) => navigate(`/investigations/${res.job_id}`),
  });
  return (
    <button
      type="button"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      title="פתח חקירת עומק על תחזית זו"
      aria-label="פתח חקירת עומק על תחזית זו"
      className="flex shrink-0 items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken disabled:opacity-60"
    >
      <Search size={12} aria-hidden="true" />
      {mutation.isPending ? "פותח…" : "חפירה"}
    </button>
  );
}

function ForecastSources({ sources }: { sources: string[] }) {
  const resolved = useResolvedSources(sources);
  if (resolved.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-3 border-t border-border pt-2 text-xs">
      {resolved.map((r, i) =>
        r.url ? (
          // R10-preview (2026-09-07): read the summary before leaving -- hover/focus (desktop) or
          // a first tap (touch) shows it before the link's own click actually opens the source.
          <SourcePreviewPopover
            key={r.key + i}
            itemId={r.itemId}
            fallback={{ title: r.title, url: r.url }}
          >
            <a
              href={r.url}
              target="_blank"
              rel="noopener noreferrer"
              title={r.title}
              className="flex items-center gap-1 text-accent hover:underline"
              dir="ltr"
            >
              <bdi dir="auto">{r.label}</bdi>
              <ExternalLink size={11} aria-hidden="true" />
            </a>
          </SourcePreviewPopover>
        ) : (
          <span key={r.key + i} title={r.title} className="text-fg-dim">
            <bdi dir="auto">{r.label}</bdi>
          </span>
        ),
      )}
    </div>
  );
}

function ForecastCardView({ f }: { f: ForecastCard }) {
  return (
    <div className="space-y-2.5 rounded-lg border border-border bg-bg-raised p-4 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <bdi className="block text-xs text-fg-dim" dir="auto">
            {f.platform}
          </bdi>
          <bdi className="block font-medium text-fg" dir="auto">
            {f.payload_need}
          </bdi>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {f.buyer_country && (
            <span className="flex items-center gap-1 rounded-md bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted">
              <span aria-hidden="true">{countryFlagEmoji(f.buyer_country)}</span>
              <span className="font-mono">{f.buyer_country}</span>
            </span>
          )}
          <DeepDiveButton f={f} />
        </div>
      </div>

      <LikelihoodMeter value={f.likelihood} />

      <p className="text-xs text-fg-dim">
        חלון זמן: <span className="font-mono">{formatDate(f.window_from)}</span> –{" "}
        <span className="font-mono">{formatDate(f.window_to)}</span>
      </p>

      {f.candidate_vendors.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {f.candidate_vendors.map((v) => (
            <span key={v} className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted">
              <bdi>{v}</bdi>
            </span>
          ))}
        </div>
      )}

      <ForecastRationale text={f.rationale_he} />

      <ForecastSources sources={f.sources} />
    </div>
  );
}

export function ForecastExplanationBanner() {
  return (
    <div className="rounded-lg border border-accent/30 bg-accent-muted/10 p-3 text-xs leading-relaxed text-fg-dim">
      <bdi className="font-semibold text-fg" dir="auto">
        איך נבנית התחזית:{" "}
      </bdi>
      <bdi dir="auto">
        הסבירות, חלון הזמן וספקי המועמדים מחושבים בכללים דטרמיניסטיים על בסיס אותות בשוק (מחזורי
        רכש קודמים, אירועים מפעילים, דפוסי תחזוקה); הנימוק בעברית (rationale) נכתב ע״י מודל שפה
        ומצטט תמיד את פריטי המקור בסימון [item N] — לעולם לא ממציא עובדה שאינה מגובה בנתונים.
      </bdi>
    </div>
  );
}

export function ForecastList({ forecasts }: { forecasts: ForecastCard[] }) {
  if (forecasts.length === 0) {
    return (
      <EmptyState
        title="אין תחזיות מכרזים כרגע"
        description="תחזיות מכרזים מופקות בריצה הלילית (FR-5.2) מזיהוי אותות רכש; אין כרגע רשומות."
      />
    );
  }

  return (
    <div className="space-y-4">
      <ForecastExplanationBanner />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {forecasts.map((f) => (
          <ForecastCardView key={f.id} f={f} />
        ))}
      </div>
    </div>
  );
}
