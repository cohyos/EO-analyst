import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import type { ForecastCard } from "@/types/api";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import {
  LIKELIHOOD_BAND_BAR_CLASS,
  LIKELIHOOD_BAND_CHIP_CLASS,
  likelihoodBand,
} from "@/lib/tenders";
import { EmptyState } from "@/components/states";

const RATIONALE_ITEM_RE = /(\[item \d+\])/g;
const RATIONALE_ITEM_MATCH_RE = /^\[item (\d+)\]$/;

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
        {f.buyer_country && (
          <span className="flex shrink-0 items-center gap-1 rounded-md bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted">
            <span aria-hidden="true">{countryFlagEmoji(f.buyer_country)}</span>
            <span className="font-mono">{f.buyer_country}</span>
          </span>
        )}
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

      {f.sources.length > 0 && (
        <div className="flex flex-wrap gap-3 border-t border-border pt-2 text-xs">
          {f.sources.map((s, i) => (
            <a
              key={i}
              href={s}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-accent hover:underline"
              dir="ltr"
            >
              מקור {i + 1}
              <ExternalLink size={11} aria-hidden="true" />
            </a>
          ))}
        </div>
      )}
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
