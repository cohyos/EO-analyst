import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Conference } from "@/types/api";

const getConferences = vi.fn();
const getConferencesIcalUrl = vi.fn(() => "/api/conferences/ical");

vi.mock("@/api", () => ({
  api: {
    getConferences: (...args: unknown[]) => getConferences(...args),
    getConferencesIcalUrl: () => getConferencesIcalUrl(),
  },
}));

const downloadConferenceIcs = vi.fn();
vi.mock("@/lib/ics", () => ({
  downloadConferenceIcs: (...args: unknown[]) => downloadConferenceIcs(...args),
}));

import { ConferencesPage } from "./ConferencesPage";

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
    early_bird_deadline: "2026-08-01",
    cfp_deadline: null,
    cost_range: "$500-1200",
    registration_url: null,
    entry_conditions: "הזמנה בלבד",
    status: "estimated",
    last_verified_at: null,
    changes: { start_date: { from: "2026-09-15", to: "2026-10-01" } },
    ...over,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConferencesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getConferences.mockReset();
  downloadConferenceIcs.mockReset();
});

describe("ConferencesPage", () => {
  it("shows the real-API empty state when there are no conferences in range", async () => {
    getConferences.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("אין כנסים קרובים")).toBeInTheDocument();
  });

  it("renders the conference name as plain text when no outbound URL is available", async () => {
    getConferences.mockResolvedValue([makeConference({ registration_url: null, url: null })]);
    renderPage();
    await screen.findByText("AUSA 2026");
    expect(screen.queryByRole("link", { name: /AUSA 2026/ })).not.toBeInTheDocument();
  });

  it("links the conference name out to registration_url when present", async () => {
    getConferences.mockResolvedValue([
      makeConference({ registration_url: "https://ausa.test/register" }),
    ]);
    renderPage();
    const link = await screen.findByTitle("פתח קישור הרשמה/מקור בכרטיסייה חדשה");
    expect(link).toHaveAttribute("href", "https://ausa.test/register");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("triggers an ICS download without navigating when 'הוסף ליומן' is clicked", async () => {
    const conf = makeConference();
    getConferences.mockResolvedValue([conf]);
    renderPage();
    fireEvent.click(await screen.findByTitle("הוסף ליומן (ICS)"));
    expect(downloadConferenceIcs).toHaveBeenCalledWith(conf);
  });

  // R6-ui (07-conferences.spec.ts, mobile-390x844 + iphone-safari): the name used to be the
  // *entire* outbound-link anchor, so on narrow viewports (where the table's min-width forces the
  // name column to dominate whatever's visible before any horizontal scroll) a plain tap meant to
  // expand the row landed on the link and navigated away instead of toggling. The link is now a
  // small icon-only affordance separate from the name text, so a click on the name itself must
  // toggle the row, while a click on the link icon must still open the URL without toggling.
  it("clicking the outbound-link icon does not toggle the row, but clicking the name text does", async () => {
    getConferences.mockResolvedValue([
      makeConference({ registration_url: "https://ausa.test/register" }),
    ]);
    renderPage();
    const nameText = await screen.findByText("AUSA 2026");
    const row = nameText.closest("tr")!;
    const link = screen.getByTitle("פתח קישור הרשמה/מקור בכרטיסייה חדשה");

    fireEvent.click(link);
    expect(row).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(nameText);
    expect(row).toHaveAttribute("aria-expanded", "true");
  });

  it("expands a details row on click showing thresholds/changes, and collapses on a second click", async () => {
    getConferences.mockResolvedValue([makeConference()]);
    renderPage();
    const row = (await screen.findByText("AUSA 2026")).closest("tr")!;

    expect(screen.queryByText(/Early bird/)).not.toBeInTheDocument();
    fireEvent.click(row);
    expect(screen.getByText(/Early bird/)).toBeInTheDocument();
    expect(screen.getByText("הזמנה בלבד")).toBeInTheDocument();
    expect(screen.getByText("2026-09-15")).toBeInTheDocument();

    fireEvent.click(row);
    expect(screen.queryByText(/Early bird/)).not.toBeInTheDocument();
  });
});
