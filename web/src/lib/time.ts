// Time formatting helpers. All backend timestamps are ISO-8601 with offset
// (UTC); display is always Asia/Jerusalem per docs/CONVENTIONS.md rule 7.

const TZ = "Asia/Jerusalem";

// Content review (docs/qa/content_review/CR-ui.md): every one of these formatters returns a
// short numeric/punctuation string (`03.09.2026, 11:12`) with no strong-direction character of
// its own. Dropped as plain text into the app's RTL flow (most call sites are a bare
// `{formatDateTime(x)}` inside a Hebrew sentence or flex row, with no `<bdi>` wrapper), the UAX#9
// bidi algorithm has no strong character to anchor the run to and can reorder or line-wrap it
// unpredictably -- e.g. the separating comma ending up stranded on its own line, ahead of the
// date instead of after it. Wrapping the return value in LRI/PDI (U+2066/U+2069, the Unicode
// equivalent of `<bdi dir="ltr">…</bdi>`, but usable in plain strings/text nodes with no markup)
// isolates it as an atomic LTR run wherever it lands, fixing every call site at once instead of
// auditing ~40 individual usages across the app. Invisible in rendered output and in copy-pasted
// text, so it does not change what a test or a user reading the string sees.
const LRI = "⁦";
const PDI = "⁩";
function isolateLtr(s: string): string {
  return `${LRI}${s}${PDI}`;
}

// W25 (docs/REVIEW_2026-09-06_evening.md): these three formatters always rendered dates with the
// `he-IL` `Intl` locale regardless of the active app locale (English mode audit finding — dates
// stayed in Hebrew locale numeral/ordering conventions even with `en` selected). Every call site
// still works unchanged (the param is optional, defaulting to the original `he-IL` behavior);
// pass the app's active locale (`useI18n().locale`, `"he" | "en"`) from a component that has
// been migrated to respect it. `"en"` maps to `en-GB` (day-month-year, like the existing he-IL
// ordering) rather than `en-US`, so switching locale only changes language, not field order.
function _intlLocale(locale?: "he" | "en"): string {
  return locale === "en" ? "en-GB" : "he-IL";
}

export function formatDateTime(iso: string | null | undefined, locale?: "he" | "en"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return isolateLtr(
    new Intl.DateTimeFormat(_intlLocale(locale), {
      timeZone: TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(d),
  );
}

export function formatDate(iso: string | null | undefined, locale?: "he" | "en"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return isolateLtr(
    new Intl.DateTimeFormat(_intlLocale(locale), {
      timeZone: TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(d),
  );
}

export function formatTime(iso: string | null | undefined, locale?: "he" | "en"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return isolateLtr(
    new Intl.DateTimeFormat(_intlLocale(locale), {
      timeZone: TZ,
      hour: "2-digit",
      minute: "2-digit",
    }).format(d),
  );
}

/** `m:ss` (or `h:mm:ss` past an hour) duration between two ISO timestamps -- deliberately
 * locale-agnostic (digits + colons only, no unit words) so the jobs table (W20) can show how
 * long a job took/has been running without needing per-locale translation. `finishedAt` absent
 * means "still running" -- duration is measured against `Date.now()` instead. */
export function formatDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string | null {
  if (!startedAt) return null;
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return null;
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  if (Number.isNaN(end)) return null;
  const totalSec = Math.max(0, Math.floor((end - start) / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return isolateLtr(h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`);
}

/** Relative "time ago" in Hebrew, e.g. "לפני 5 דק'" / "לפני 3 שע'". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const diffMs = Date.now() - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "עכשיו";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `לפני ${diffMin} דק׳`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `לפני ${diffHr} שע׳`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `לפני ${diffDay} ימים`;
  const diffMonth = Math.floor(diffDay / 30);
  return `לפני ${diffMonth} חוד׳`;
}
