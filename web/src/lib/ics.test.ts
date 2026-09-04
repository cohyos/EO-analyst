import { describe, expect, it } from "vitest";
import { buildConferenceIcs, escapeIcsText, nextIcsDate, toIcsDate } from "./ics";
import type { Conference } from "@/types/api";

function makeConference(over: Partial<Conference> = {}): Conference {
  return {
    id: 1,
    name: "AUSA 2026",
    location: "Washington",
    starts_at: "2026-10-01",
    ends_at: "2026-10-18",
    url: null,
    relevance_he: "גבוהה (4)",
    organizer: null,
    start_date: "2026-10-01",
    end_date: "2026-10-18",
    city: "Washington",
    venue: null,
    cadence: "annual",
    relevance: 4,
    rationale: "מועד משוער לפי מחזוריות היסטורית",
    registration_opens: null,
    early_bird_deadline: null,
    cfp_deadline: null,
    cost_range: null,
    registration_url: null,
    entry_conditions: null,
    status: "estimated",
    last_verified_at: null,
    changes: {},
    ...over,
  };
}

describe("escapeIcsText", () => {
  it("escapes commas, semicolons, backslashes and newlines per RFC 5545", () => {
    expect(escapeIcsText("a,b;c\\d\ne")).toBe("a\\,b\\;c\\\\d\\ne");
  });
});

describe("toIcsDate / nextIcsDate", () => {
  it("converts an ISO date to a compact ICS DATE", () => {
    expect(toIcsDate("2026-10-01")).toBe("20261001");
  });

  it("adds one day for the exclusive DTEND", () => {
    expect(nextIcsDate("2026-10-18")).toBe("20261019");
  });

  it("rolls over a month/year boundary", () => {
    expect(nextIcsDate("2026-12-31")).toBe("20270101");
  });
});

describe("buildConferenceIcs", () => {
  it("produces a VCALENDAR with a single all-day VEVENT for the conference span", () => {
    const ics = buildConferenceIcs(makeConference());
    expect(ics).toContain("BEGIN:VCALENDAR");
    expect(ics).toContain("BEGIN:VEVENT");
    expect(ics).toContain("UID:conference-1@eo-analyst");
    expect(ics).toContain("DTSTART;VALUE=DATE:20261001");
    expect(ics).toContain("DTEND;VALUE=DATE:20261019"); // exclusive: end_date + 1
    expect(ics).toContain("SUMMARY:AUSA 2026");
    expect(ics).toContain("LOCATION:Washington");
    expect(ics).toContain("END:VEVENT");
    expect(ics).toContain("END:VCALENDAR");
    expect(ics.endsWith("\r\n")).toBe(true);
  });

  it("prefers registration_url over the legacy url field", () => {
    const ics = buildConferenceIcs(
      makeConference({ url: "https://legacy.test", registration_url: "https://register.test" }),
    );
    expect(ics).toContain("URL:https://register.test");
    expect(ics).not.toContain("URL:https://legacy.test");
  });

  it("escapes a comma-bearing name in SUMMARY", () => {
    const ics = buildConferenceIcs(makeConference({ name: "AUSA, Annual Meeting" }));
    expect(ics).toContain("SUMMARY:AUSA\\, Annual Meeting");
  });

  it("omits DTSTART/DTEND entirely when no date is available", () => {
    const ics = buildConferenceIcs(
      makeConference({ start_date: null, end_date: null, starts_at: "", ends_at: "" }),
    );
    expect(ics).not.toContain("DTSTART");
    expect(ics).not.toContain("DTEND");
  });
});
