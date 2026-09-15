import { Outlet, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { NavRail } from "./NavRail";
import { MobileNav } from "./MobileNav";
import { TopBar } from "./TopBar";
import { StatusStrip } from "./StatusStrip";
import { ChatPanel } from "./ChatPanel";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { useStatusSocket } from "@/hooks/useStatusSocket";
import { useUiStore } from "@/store/uiStore";
import { useI18n } from "@/i18n";

// Shared layout constants for the phone bottom-nav era (defects #3/#4,
// docs/qa/content_review/UI-MOBILE-iphone.md). Below `md:` the desktop
// `NavRail` becomes `MobileNav`'s fixed bottom tab bar, which needs
// reserved space carved out of the viewport (not just floated on top —
// that would hide `StatusStrip`, the last item in this column's normal
// flow). Defining the heights once as CSS custom properties on the shell
// root and consuming them everywhere (here, MobileNav.tsx, ChatPanel.tsx)
// keeps the reserved space, the tab bar's own height, and the FAB's offset
// from drifting out of sync.
const MOBILE_LAYOUT_VARS = {
  "--eoa-tabbar-h": "3.5rem",
  "--eoa-fab-h": "2.75rem",
  // Clearance for `StatusStrip`'s own (variable) height below `md:` — its
  // footer row alone is ~2.25rem, but the "local inference paused" banner
  // (StatusStrip.tsx, `gate.local_inference_paused`) adds a second row on
  // top of it that the ChatPanel FAB's original fixed offset (tuned for the
  // banner-less case) didn't budget for, so the FAB ended up overlapping the
  // banner text. Generous on purpose since it's a static constant, not a
  // measurement of the actual (conditionally taller) strip.
  "--eoa-statusbar-clear-h": "6rem",
} as React.CSSProperties;

export function AppShell() {
  const statusState = useStatusSocket();
  const theme = useUiStore((s) => s.theme);
  const location = useLocation();
  const { locale, dir } = useI18n();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    // Keep the browser-chrome / status-bar color meta tag in sync with the
    // app's own light/dark toggle (this app has a manual switch independent
    // of OS prefers-color-scheme, so a static media-query pair in index.html
    // can't track it) — matters most on iOS Safari, where the top status
    // bar area picks up this color.
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", theme === "light" ? "#f4f6f7" : "#0b1113");
    }
  }, [theme]);

  return (
    // 100dvh (not 100vh) so iOS Safari's collapsing/expanding address bar
    // doesn't leave a permanent gap under the status strip or clip content
    // behind the chrome — see docs/CONVENTIONS.md task notes on iOS viewport units.
    //
    // `pb-[…] md:pb-0` reserves space for MobileNav's `fixed bottom-0` tab bar
    // below `md:` (see MOBILE_LAYOUT_VARS above): without it the fixed bar
    // would render on top of StatusStrip, which is otherwise the last item
    // in this column's normal flow and would sit exactly where the tab bar
    // is pinned.
    <div
      className="flex h-dvh flex-col pb-[calc(var(--eoa-tabbar-h)+env(safe-area-inset-bottom))] md:pb-0"
      style={MOBILE_LAYOUT_VARS}
      dir={dir}
      lang={locale}
    >
      <div className="flex min-h-0 flex-1">
        <NavRail />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar nightWindow={statusState.status?.pipeline.night_window ?? false} />
          {/* Bottom padding below `md:` reserves room for the fixed MobileNav tab
              bar + the floating ChatPanel pill (defect #3) so neither hides the
              tail of the page's own content. Matches the pill's own top edge
              (`--eoa-statusbar-clear-h` + `--eoa-tabbar-h` + `--eoa-fab-h`,
              see ChatPanel.tsx), the highest of the two fixed overlays. */}
          <main className="relative min-h-0 flex-1 overflow-y-auto bg-bg pb-[calc(var(--eoa-statusbar-clear-h)+var(--eoa-tabbar-h)+var(--eoa-fab-h)+env(safe-area-inset-bottom))] md:pb-0">
            <ErrorBoundary key={location.pathname}>
              <Outlet context={statusState} />
            </ErrorBoundary>
          </main>
        </div>
        <ChatPanel />
      </div>
      <StatusStrip state={statusState} />
      <MobileNav />
      <CommandPalette />
    </div>
  );
}
