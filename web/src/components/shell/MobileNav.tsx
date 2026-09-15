import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { MoreHorizontal, X } from "lucide-react";
import { useMobileNavGroups } from "./nav";
import { useT } from "@/i18n";
import { cn } from "@/lib/cn";

function routeMatches(item: { to: string; end?: boolean }, pathname: string): boolean {
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

/**
 * Phone bottom tab bar + "more" sheet — replaces the desktop `NavRail` icon
 * column below `md:` (defect #4, docs/qa/content_review/UI-MOBILE-iphone.md:
 * the side rail was eating ~64px/390px on every phone screen and never
 * collapsed). Routes come from `useMobileNavGroups()` (nav.ts), which slices
 * the same `NAV_ROUTES` table `NavRail` uses, so the two navs can't drift.
 */
export function MobileNav() {
  const t = useT();
  const { primary, more } = useMobileNavGroups();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = more.some((item) => routeMatches(item, location.pathname));

  // Close the sheet on navigation (selecting a route) and don't leave it
  // open across an unrelated route change triggered elsewhere.
  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!moreOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setMoreOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [moreOpen]);

  return (
    <>
      <nav
        aria-label={t("nav.ariaLabel")}
        // `fixed … bottom-0` per the fix brief; `--eoa-tabbar-h` (set on AppShell's
        // root, see AppShell.tsx) is the single source of truth for this bar's
        // reserved height, shared with the `<main>` bottom padding and the
        // ChatPanel FAB offset so the three never drift out of sync.
        className="fixed inset-x-0 bottom-0 z-30 flex h-[var(--eoa-tabbar-h)] items-stretch border-t border-border bg-bg-raised pb-[env(safe-area-inset-bottom)] md:hidden"
      >
        {primary.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "tap-target flex flex-1 flex-col items-center justify-center gap-0.5 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                  isActive ? "text-accent" : "text-fg-muted",
                )
              }
            >
              <Icon size={20} aria-hidden="true" />
              <span className="max-w-full truncate px-1 leading-tight">{item.label}</span>
            </NavLink>
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          data-testid="mobile-nav-more"
          className={cn(
            "tap-target flex flex-1 flex-col items-center justify-center gap-0.5 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
            moreActive ? "text-accent" : "text-fg-muted",
          )}
        >
          <MoreHorizontal size={20} aria-hidden="true" />
          <span className="max-w-full truncate px-1 leading-tight">{t("nav.more")}</span>
        </button>
      </nav>

      {moreOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMoreOpen(false)}
            aria-hidden="true"
            data-testid="mobile-nav-more-backdrop"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("shell.moreSheetTitle")}
            data-testid="mobile-nav-more-sheet"
            className="absolute inset-x-0 bottom-0 max-h-[75vh] overflow-y-auto rounded-t-xl border-t border-border bg-bg-raised pb-[env(safe-area-inset-bottom)] shadow-panel"
          >
            <div className="sticky top-0 flex items-center justify-between border-b border-border bg-bg-raised px-4 py-3">
              <h2 className="text-sm font-semibold">{t("shell.moreSheetTitle")}</h2>
              <button
                type="button"
                onClick={() => setMoreOpen(false)}
                aria-label={t("shell.moreSheetClose")}
                className="tap-target inline-flex items-center justify-center rounded-md p-1.5 text-fg-dim hover:bg-bg-sunken hover:text-fg"
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <ul className="grid grid-cols-2 gap-1 p-2">
              {[...primary, ...more].map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) =>
                        cn(
                          "tap-target flex items-center gap-2 rounded-md px-3 py-2.5 text-sm",
                          isActive ? "bg-accent-muted text-accent-fg" : "text-fg hover:bg-bg-sunken",
                        )
                      }
                    >
                      <Icon size={18} aria-hidden="true" className="shrink-0" />
                      <span className="truncate">{item.label}</span>
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
