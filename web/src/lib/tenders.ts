// Shared display helpers for the /tenders screen (section 5.2 / FR-5.2).
import type { TenderIntake, TenderStatus } from "@/types/api";

// Content review (docs/qa/content_review/CR-ui.md): `tender.source` (`GET /api/tenders`) is the
// raw connector id from `config/tenders.yaml` (Python side, out of scope for this UI-only pass) --
// e.g. `rfi_rfp_news`, `ted_eu`, `jp_search` -- not a display name. The table used to render that
// slug as-is, which read as a raw untranslated key sitting right next to properly-labeled columns.
// Mirrors each connector's own `name:` from `config/tenders.yaml` (shortened to the part before
// " -- ", which is usually already a recognizable outlet/portal name on its own); an id added to
// the config after this map was written falls back to the raw slug rather than guessing at a
// label. `tenderSourceLabel` always returns a short label meant to sit in a table cell.
const TENDER_SOURCE_LABEL: Record<string, string> = {
  ted_eu: "TED (Tenders Electronic Daily)",
  ted_eu_cpv: "TED (Tenders Electronic Daily)",
  uk_contracts_finder: "UK Contracts Finder (OCDS)",
  uk_find_tender: "UK Find a Tender Service (FTS)",
  sam_gov_api: "SAM.gov Opportunities API (US)",
  sam_gov_search: "SAM.gov opportunities",
  il_mod: "אתר מכרזים — משרד הביטחון",
  il_mod_search: "מכרזי משרד הביטחון",
  nato_nspa: "NATO Support and Procurement Agency (NSPA)",
  nato_ncia: "NATO Communications and Information Agency (NCIA)",
  nato_search: "NATO NSPA/NCIA procurement",
  canada_buys: "CanadaBuys",
  canada_buys_search: "CanadaBuys",
  austender: "AusTender",
  austender_search: "AusTender",
  rfi_rfp_news: "RFI/RFP defense news",
  rfi_rfp_news_he: "RFI/RFP הודעות ביטחוניות",
  fr_boamp: "France BOAMP",
  nl_tenderned: "Netherlands TenderNed",
  es_placsp_atom: "Spain PLACSP",
  us_grants_gov: "US Grants.gov",
  us_sbir_gov: "US SBIR.gov",
  no_doffin_api: "Norway Doffin",
  pl_ezamowienia_api: "Poland eZamówienia",
  eu_sedia_funding_tenders: "EU Funding & Tenders Portal",
  us_usaspending: "US USAspending.gov",
  us_defense_innovation_search: "US defense innovation opportunities",
  de_search: "Germany bund.de / evergabe-online",
  it_search: "Italy Consip/MePA",
  no_search: "Norway Doffin",
  fi_search: "Finland Hilma",
  dk_search: "Denmark Udbud.dk",
  pl_search: "Poland eZamówienia/BZP",
  jp_search: "Japan ATLA/MoD procurement",
  kr_search: "Korea KONEPS / D2B",
  in_search: "India GeM / MoD RFI",
  sg_search: "Singapore GeBIZ",
  gcc_search: "UAE/Saudi Etimad",
  nz_search: "New Zealand GETS",
  ungm_search: "UN Global Marketplace (UNGM)",
  eda_search: "NATO EDA / EDF calls",
};

export function tenderSourceLabel(source: string | null | undefined): string {
  if (!source) return "—";
  return TENDER_SOURCE_LABEL[source] ?? source;
}

export const TENDER_STATUS_LABEL: Record<TenderStatus, string> = {
  open: "פתוח",
  closed: "סגור",
  awarded: "הוענק",
  unknown: "לא ידוע",
  archived: "בארכיון",
};

export const TENDER_STATUS_CHIP_CLASS: Record<TenderStatus, string> = {
  open: "bg-ok/15 text-ok",
  closed: "bg-bg-sunken text-fg-dim",
  awarded: "bg-accent-muted text-accent",
  unknown: "bg-warn/15 text-warn",
  archived: "bg-bg-sunken text-fg-dim",
};

// F24: the tenders board's default (no explicit status filter) view -- 'open' and recently-seen
// 'unknown' tenders only. Mirrors eoa.api.services.DEFAULT_STATUSES.
export const TENDER_DEFAULT_VIEW_STATUSES: TenderStatus[] = ["open", "unknown"];

// W2b (open intake, 2026-09-06 evening): only 'candidate' gets its own visible badge -- 'accepted'
// is the normal/expected state (no badge needed) and 'rejected-by-user' is hidden by default
// (services.list_tenders never returns it), so there is no row to badge in the first place.
export const TENDER_CANDIDATE_BADGE_LABEL = "מועמד";
export const TENDER_INTAKE_CHIP_CLASS: Record<TenderIntake, string> = {
  candidate: "bg-warn/15 text-warn",
  accepted: "bg-ok/15 text-ok",
  "rejected-by-user": "bg-bg-sunken text-fg-dim",
};

/** 0-1 relevance_score as a rounded percentage string, e.g. 0.62 -> "62%". Returns "—" for null
 * (an LLM-unclassified notice's score should never actually be null post-migration-0021, but the
 * API type allows it defensively). */
export function relevanceScorePercent(score: number | null | undefined): string {
  if (score == null || Number.isNaN(score)) return "—";
  return `${Math.round(score * 100)}%`;
}

/** Days-left urgency threshold shared by the tenders table chip and the Morning tile. */
export const DEADLINE_URGENT_DAYS = 14;
export const DEADLINE_SOON_DAYS = 30;

/**
 * Whole days between `now` and `deadline` (a `DATE`-only ISO string, e.g.
 * "2026-09-20"). Computed in UTC calendar days on both sides so a bare date
 * string never picks up a timezone-shifted off-by-one against the local
 * `now` (per docs/CONVENTIONS.md rule 7, display stays Asia/Jerusalem, but
 * day-granularity arithmetic is timezone-agnostic by construction here).
 * Returns null for a missing/unparseable deadline. Negative means overdue.
 */
export function daysLeft(
  deadline: string | null | undefined,
  now: Date = new Date(),
): number | null {
  if (!deadline) return null;
  const d = new Date(deadline);
  if (Number.isNaN(d.getTime())) return null;
  const utcDeadline = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const utcNow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((utcDeadline - utcNow) / 86_400_000);
}

export type LikelihoodBand = "high" | "mid" | "low";

/** Bands a 0-1 likelihood into high/mid/low for color coding (>=0.66 / >=0.33 / below). */
export function likelihoodBand(value: number | null | undefined): LikelihoodBand {
  const v = value ?? 0;
  if (v >= 0.66) return "high";
  if (v >= 0.33) return "mid";
  return "low";
}

export const LIKELIHOOD_BAND_FG_CLASS: Record<LikelihoodBand, string> = {
  high: "text-level-red",
  mid: "text-level-orange",
  low: "text-level-archive",
};

export const LIKELIHOOD_BAND_CHIP_CLASS: Record<LikelihoodBand, string> = {
  high: "bg-level-red-bg text-level-red",
  mid: "bg-level-orange-bg text-level-orange",
  low: "bg-level-archive-bg text-level-archive",
};

export const LIKELIHOOD_BAND_BAR_CLASS: Record<LikelihoodBand, string> = {
  high: "bg-level-red",
  mid: "bg-level-orange",
  low: "bg-level-archive",
};
