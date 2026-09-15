import {
  Bot,
  Briefcase,
  CalendarDays,
  Camera,
  FileSearch,
  Gavel,
  Inbox,
  LayoutDashboard,
  Layers,
  MessageSquareText,
  Network,
  Radar,
  Scale,
  Search,
  Settings as SettingsIcon,
  Telescope,
  FileText,
} from "lucide-react";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

export interface NavItem {
  to: string;
  end?: boolean;
  labelKey: TranslationKey;
  icon: typeof LayoutDashboard;
}

// Route/icon table only — labels are resolved through `t()` at render time
// (U6) via `useNavItems()`/`usePageTitle()` below, so both the nav rail and
// the top-bar heading follow the active locale.
const NAV_ROUTES: NavItem[] = [
  { to: "/", end: true, labelKey: "nav.morning", icon: LayoutDashboard },
  { to: "/feed", labelKey: "nav.feed", icon: Search },
  { to: "/entities", labelKey: "nav.entities", icon: Network },
  { to: "/investigations", labelKey: "nav.investigations", icon: Telescope },
  { to: "/ask", labelKey: "nav.ask", icon: MessageSquareText },
  { to: "/conferences", labelKey: "nav.conferences", icon: CalendarDays },
  { to: "/tenders", labelKey: "nav.tenders", icon: Gavel },
  { to: "/patents", labelKey: "nav.patents", icon: Scale },
  { to: "/payloads", labelKey: "nav.payloads", icon: Camera },
  { to: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { to: "/reports", labelKey: "nav.reports", icon: FileText },
  { to: "/bd", labelKey: "nav.bd", icon: Briefcase },
  { to: "/product-lines", labelKey: "nav.productLines", icon: Layers },
  { to: "/dossiers", labelKey: "nav.dossiers", icon: FileSearch },
  { to: "/tech-radar", labelKey: "nav.techRadar", icon: Radar },
  { to: "/settings", labelKey: "nav.settings", icon: SettingsIcon },
];

/** Resolved (icon + localized label) nav entries for `NavRail`. */
export function useNavItems(): Array<{ to: string; end?: boolean; label: string; icon: typeof LayoutDashboard }> {
  const t = useT();
  return NAV_ROUTES.map((item) => ({ to: item.to, end: item.end, label: t(item.labelKey), icon: item.icon }));
}

// The phone bottom tab bar (NavRail.tsx's `MobileNav`, defect #4 in
// docs/qa/content_review/UI-MOBILE-iphone.md) only has room for a handful of
// slots, so it surfaces the routes analysts open most (morning briefing,
// triage feed, reports, product dossiers) plus a "more" button for the rest —
// picked from the very `NAV_ROUTES` table above so the two navs never drift.
const MOBILE_PRIMARY_ROUTE_PATHS = ["/", "/feed", "/reports", "/dossiers"] as const;

/** `{ primary, more }` nav entries for the phone bottom tab bar + its overflow sheet. */
export function useMobileNavGroups(): {
  primary: Array<{ to: string; end?: boolean; label: string; icon: typeof LayoutDashboard }>;
  more: Array<{ to: string; end?: boolean; label: string; icon: typeof LayoutDashboard }>;
} {
  const items = useNavItems();
  const primary = MOBILE_PRIMARY_ROUTE_PATHS.map((to) => items.find((item) => item.to === to)).filter(
    (item): item is (typeof items)[number] => item != null,
  );
  const more = items.filter((item) => !(MOBILE_PRIMARY_ROUTE_PATHS as readonly string[]).includes(item.to));
  return { primary, more };
}

/** Localized page heading for the given pathname, used by `TopBar`'s `<h1>`. */
export function usePageTitle(pathname: string): string {
  const t = useT();
  if (pathname === "/") return t("nav.morning");
  if (pathname.startsWith("/items")) return t("nav.itemDetail");
  if (pathname.startsWith("/feed")) return t("nav.feed");
  if (pathname.startsWith("/entities")) return t("nav.entities");
  if (pathname.startsWith("/investigations")) return t("nav.investigations");
  if (pathname.startsWith("/ask")) return t("nav.ask");
  if (pathname.startsWith("/conferences")) return t("nav.conferences");
  if (pathname.startsWith("/tenders")) return t("nav.tenders");
  if (pathname.startsWith("/patents")) return t("nav.patents");
  if (pathname.startsWith("/payloads")) return t("nav.payloads");
  if (pathname.startsWith("/inbox")) return t("nav.inbox");
  if (pathname.startsWith("/reports")) return t("nav.reports");
  if (pathname.startsWith("/bd")) return t("nav.bd");
  if (pathname.startsWith("/product-lines")) return t("nav.productLines");
  if (pathname.startsWith("/dossiers")) return t("nav.dossiers");
  if (pathname.startsWith("/tech-radar")) return t("nav.techRadar");
  if (pathname.startsWith("/settings")) return t("nav.settings");
  return t("nav.shellFallback");
}

export { Bot };
