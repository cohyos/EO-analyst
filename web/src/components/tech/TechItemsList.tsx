import { Link } from "react-router-dom";
import type { ItemCard, TechActorKind, TechMaturity } from "@/types/api";
import { timeAgo } from "@/lib/time";

const ACTOR_LABEL_HE: Record<TechActorKind, string> = {
  academia: "אקדמיה",
  lab: "מעבדה",
  startup: "סטארטאפ",
  prime: "יצרן ביטחוני",
  government: "ממשלתי",
};

const MATURITY_LABEL_HE: Record<TechMaturity, string> = {
  lab: "מעבדה",
  prototype: "אב-טיפוס",
  qualified: "מוסמך",
  fielded: "מבצעי",
};

/** A12 (מעקב טכנולוגי): the radar's click-through item list -- title, actor, TRL/maturity,
 * so-what, and a link into the full item detail page. A lighter-weight sibling of
 * `components/feed/FeedRow` (no selection/rating/drag-and-drop -- those are Feed-page-specific),
 * kept consistent with how `components/tenders/TenderTable` renders its own domain's rows. */
export function TechItemsList({ items }: { items: ItemCard[] }) {
  if (items.length === 0) {
    return <p className="p-4 text-sm text-fg-dim">אין פריטים תואמים לסינון הנוכחי.</p>;
  }
  return (
    <ul className="divide-y divide-border rounded-lg border border-border" role="list">
      {items.map((it) => (
        <li key={it.id} className="flex flex-col gap-1 p-3 text-sm" data-testid={`tech-item-${it.id}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link to={`/items/${it.id}`} className="font-medium text-fg hover:underline">
              {it.title || "(ללא כותרת)"}
            </Link>
            <span className="text-xs text-fg-dim">{timeAgo(it.published_at)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
            <span>{it.source_name}</span>
            {it.tech_actor_kind && <span>שחקן: {ACTOR_LABEL_HE[it.tech_actor_kind] ?? it.tech_actor_kind}</span>}
            <span>
              TRL / בגרות: {it.trl ?? "—"} / {it.tech_maturity ? (MATURITY_LABEL_HE[it.tech_maturity] ?? it.tech_maturity) : "—"}
            </span>
          </div>
          {it.so_what_he && <p className="text-fg">{it.so_what_he}</p>}
        </li>
      ))}
    </ul>
  );
}
