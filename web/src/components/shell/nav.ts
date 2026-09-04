import {
  Bot,
  CalendarDays,
  Inbox,
  LayoutDashboard,
  MessageSquareText,
  Network,
  Search,
  Settings as SettingsIcon,
  Telescope,
  FileText,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  end?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "הבוקר", icon: LayoutDashboard, end: true },
  { to: "/feed", label: "פיד Triage", icon: Search },
  { to: "/entities", label: "ישויות וגרף", icon: Network },
  { to: "/investigations", label: "חקירות עומק", icon: Telescope },
  { to: "/ask", label: "שאל את האנליסט", icon: MessageSquareText },
  { to: "/conferences", label: "לוח כנסים", icon: CalendarDays },
  { to: "/inbox", label: "הבהרות ומשוב", icon: Inbox },
  { to: "/reports", label: "דוחות", icon: FileText },
  { to: "/settings", label: "הגדרות", icon: SettingsIcon },
];

export function pageTitleFor(pathname: string): string {
  if (pathname === "/") return "הבוקר";
  if (pathname.startsWith("/items")) return "פרטי פריט";
  if (pathname.startsWith("/feed")) return "פיד Triage";
  if (pathname.startsWith("/entities")) return "ישויות וגרף";
  if (pathname.startsWith("/investigations")) return "חקירות עומק";
  if (pathname.startsWith("/ask")) return "שאל את האנליסט";
  if (pathname.startsWith("/conferences")) return "לוח כנסים";
  if (pathname.startsWith("/inbox")) return "הבהרות ומשוב";
  if (pathname.startsWith("/reports")) return "דוחות";
  if (pathname.startsWith("/settings")) return "הגדרות";
  return "חדר מצב + עמית";
}

export { Bot };
