import type { ProductLineDetail, ReportSummary } from "@/types/api";
import { PRODUCT_LINE_CATALOG } from "@/lib/productLines";
import { mockItems } from "./items";
import { mockTenders } from "./tenders";

// PL-ui (2026-09-07): mock data for VITE_USE_MOCKS -- "קווי מוצר" (product-line status &
// business-development tracking). `mockItems`/`mockTenders` are tagged with `product_lines` at
// their own source (see the `SUBDOMAIN_TO_PRODUCT_LINES` map in `./items.ts` and the per-row
// comments in `./tenders.ts`) so the same mock rows shown here as "recent items"/"open tenders"
// are also the ones the Feed/Tenders pages' own "קו מוצר" filter matches -- one source of truth,
// no duplicated mock rows drifting out of sync.

// Hand-authored per-line flavor (subdomains/exemplar systems/competitors) -- the frozen contract
// has the backend own this in reality; these are plausible placeholders for mock mode only.
const FLAVOR: Record<
  string,
  { subdomains: string[]; exemplar_systems: string[]; competitors: string[] }
> = {
  targeting_pods: {
    subdomains: ["airborne_pods/targeting_pods"],
    exemplar_systems: ["Litening", "Sniper ATP", "Skyward"],
    competitors: ["Lockheed Martin", "Rafael Advanced Defense Systems", "Elbit Systems"],
  },
  mws_eo: {
    subdomains: ["airborne_pods/eo_warfare"],
    exemplar_systems: ["AN/AAR-60 MILDS", "PAWS-2", "Praetorian DASS"],
    competitors: ["HENSOLDT", "Leonardo DRS", "Elbit Systems"],
  },
  lorop_pods: {
    subdomains: ["airborne_pods/isr_pods"],
    exemplar_systems: ["DB-110", "CA-270", "Condor 2"],
    competitors: ["Leonardo DRS", "Teledyne FLIR", "Safran Electronics & Defense"],
  },
  eo_air_defense_warning: {
    subdomains: ["air_defense/iir_seekers", "air_defense/hel", "c_uas/detect_track"],
    exemplar_systems: ["SkySpotter", "Spexer 2000", "Skyranger 30"],
    competitors: ["Rafael Advanced Defense Systems", "HENSOLDT", "Anduril Industries"],
  },
  ball_gimbals_16in: {
    subdomains: ["airborne_pods/uav_gimbals"],
    exemplar_systems: ["MX-15", "CoMPASS", "WESCAM MX-20"],
    competitors: ["Teledyne FLIR", "Leonardo DRS", "Safran Electronics & Defense"],
  },
  border_long_range_eo: {
    subdomains: ["land_surveillance/border_towers"],
    exemplar_systems: ["IMAGEIC", "Spectro XR", "STRATOS"],
    competitors: ["HENSOLDT", "Safran Electronics & Defense", "Elbit Systems"],
  },
};

function reportFor(id: string, reportId: number, createdAt: string): ReportSummary {
  const label = FLAVOR[id] ? PRODUCT_LINE_CATALOG.find((p) => p.id === id)?.nameHe ?? id : id;
  return {
    id: reportId,
    kind: "product_line",
    period_start: "2026-06-08T00:00:00+03:00",
    period_end: "2026-09-06T00:00:00+03:00",
    path_docx: `/reports/pl_${id}_${reportId}.docx`,
    path_md: `/reports/pl_${id}_${reportId}.md`,
    path_html: `/reports/pl_${id}_${reportId}.html`,
    qa_passed: true,
    created_at: createdAt,
    headline_count: 6,
    territory: null,
    title_he: `דוח קו מוצר — ${label} — ${createdAt.slice(5, 10)}`,
    subject_he: label,
    built_at: createdAt,
    preview_he: `סקירת מצב ופיתוח עסקי לקו המוצר "${label}": פעילות מתחרים, מכרזים פתוחים ופטנטים מהחודשים האחרונים.`,
    source_count: 4,
    qa_issues: 0,
    group_key: `product_line:${id}`,
    is_latest: true,
  };
}

// Mutable per-PL report store -- `mockApi.postProductLineReport` pushes a fresh row into this
// (and flips the old `is_latest` off) so the "צור דוח" flow has something real to poll for,
// mirroring `postBdReport`'s mock behavior for a territory that already has a cached report.
export const mockProductLineReports: Record<string, ReportSummary[]> = Object.fromEntries(
  PRODUCT_LINE_CATALOG.map((p, i) => [
    p.id,
    [reportFor(p.id, 960 + i, `2026-09-0${(i % 6) + 1}T07:${10 + i}:00+03:00`)],
  ]),
);

function statsFor(id: string) {
  const items7d = mockItems.filter((it) => it.product_lines?.includes(id)).length;
  const items30d = items7d + (id.length % 5);
  const openTenders = mockTenders.filter(
    (t) => t.product_lines?.includes(id) && t.status === "open",
  ).length;
  const competitors = FLAVOR[id]?.competitors.length ?? 0;
  return {
    items_7d: items7d,
    items_30d: items30d,
    events_30d: items30d + (id.length % 3),
    open_tenders: openTenders,
    forecasts: openTenders > 0 ? 1 : 0,
    patents_90d: (id.length * 2) % 7,
    active_competitors: competitors,
  };
}

export function buildMockProductLineDetail(id: string): ProductLineDetail | null {
  const catalogEntry = PRODUCT_LINE_CATALOG.find((p) => p.id === id);
  if (!catalogEntry) return null;
  const flavor = FLAVOR[id] ?? { subdomains: [], exemplar_systems: [], competitors: [] };
  const reports = mockProductLineReports[id] ?? [];
  const latest = reports.find((r) => r.is_latest) ?? reports[0] ?? null;
  return {
    id: catalogEntry.id,
    name_he: catalogEntry.nameHe,
    name_en: catalogEntry.nameEn,
    subdomains: flavor.subdomains,
    exemplar_systems: flavor.exemplar_systems,
    competitors: flavor.competitors,
    stats: statsFor(id),
    latest_report: latest
      ? {
          id: latest.id,
          created_at: latest.created_at,
          qa_passed: latest.qa_passed,
          path_html: latest.path_html ?? "",
        }
      : null,
    recent_items: mockItems.filter((it) => it.product_lines?.includes(id)).slice(0, 8),
    open_tenders: mockTenders.filter((t) => t.product_lines?.includes(id)),
    reports,
  };
}

export function buildMockProductLines() {
  return PRODUCT_LINE_CATALOG.map((p) => {
    const detail = buildMockProductLineDetail(p.id)!;
    const { recent_items: _recent_items, open_tenders: _open_tenders, reports: _reports, ...rest } = detail;
    return rest;
  });
}
