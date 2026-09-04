import type { Conference } from "@/types/api";

/** Escapes text per RFC 5545 §3.3.11 for use inside an ICS text value. */
export function escapeIcsText(value: string): string {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\n/g, "\\n");
}

/** "2026-10-01" -> "20261001" (ICS DATE value). */
export function toIcsDate(isoDate: string): string {
  return isoDate.replace(/-/g, "").slice(0, 8);
}

/** DTEND for an all-day ICS event is exclusive, so add one calendar day. */
export function nextIcsDate(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return toIcsDate(isoDate);
  d.setUTCDate(d.getUTCDate() + 1);
  return `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, "0")}${String(
    d.getUTCDate(),
  ).padStart(2, "0")}`;
}

function nowStamp(): string {
  return new Date().toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
}

/**
 * Builds a minimal single-VEVENT .ics document for one conference — used by
 * the per-row "הוסף ליומן" button. There is no per-conference `/ical`
 * endpoint on the backend (only the full-horizon `/api/conferences/ical`),
 * so this generates the file client-side from the same `conference_card`
 * fields the list endpoint already returns.
 */
export function buildConferenceIcs(c: Conference): string {
  const start = c.start_date || c.starts_at;
  const end = c.end_date || c.ends_at || start;
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//EO-Analyst//Conferences//HE",
    "CALSCALE:GREGORIAN",
    "BEGIN:VEVENT",
    `UID:conference-${c.id}@eo-analyst`,
    `DTSTAMP:${nowStamp()}`,
  ];
  if (start) {
    lines.push(`DTSTART;VALUE=DATE:${toIcsDate(start)}`);
    lines.push(`DTEND;VALUE=DATE:${nextIcsDate(end || start)}`);
  }
  lines.push(`SUMMARY:${escapeIcsText(c.name)}`);
  if (c.location) lines.push(`LOCATION:${escapeIcsText(c.location)}`);
  if (c.rationale) lines.push(`DESCRIPTION:${escapeIcsText(c.rationale)}`);
  const url = c.registration_url || c.url;
  if (url) lines.push(`URL:${url}`);
  lines.push("END:VEVENT", "END:VCALENDAR");
  return lines.join("\r\n") + "\r\n";
}

/** Triggers a browser download of `buildConferenceIcs(c)` as `<name>.ics`. */
export function downloadConferenceIcs(c: Conference): void {
  const ics = buildConferenceIcs(c);
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${c.name.replace(/[\\/:*?"<>|]/g, "_")}.ics`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
