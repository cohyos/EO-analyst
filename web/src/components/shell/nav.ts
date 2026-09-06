import {
  Bot,
  Briefcase,
  CalendarDays,
  Gavel,
  Inbox,
  LayoutDashboard,
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
  { to: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { to: "/reports", labelKey: "nav.reports", icon: FileText },
  { to: "/bd", labelKey: "nav.bd", icon: Briefcase },
  { to: "/tech-radar", labelKey: "nav.techRadar", icon: Radar },
  { to: "/settings", labelKey: "nav.settings", icon: SettingsIcon },
];

/** Resolved (icon + localized label) nav entries for `NavRail`. */
export function useNavItems(): Array<{ to: string; end?: boolean; label: string; icon: typeof LayoutDashboard }> {
  const t = useT();
  return NAV_ROUTES.map((item) => ({ to: item.to, end: item.end, label: t(item.labelKey), icon: item.icon }));
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
  if (pathname.startsWith("/inbox")) return t("nav.inbox");
  if (pathname.startsWith("/reports")) return t("nav.reports");
  if (pathname.startsWith("/bd")) return t("nav.bd");
  if (pathname.startsWith("/tech-radar")) return t("nav.techRadar");
  if (pathname.startsWith("/settings")) return t("nav.settings");
  return t("nav.shellFallback");
}

export { Bot };
