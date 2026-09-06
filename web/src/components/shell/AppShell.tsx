import { Outlet, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { NavRail } from "./NavRail";
import { TopBar } from "./TopBar";
import { StatusStrip } from "./StatusStrip";
import { ChatPanel } from "./ChatPanel";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { useStatusSocket } from "@/hooks/useStatusSocket";
import { useUiStore } from "@/store/uiStore";
import { useI18n } from "@/i18n";

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
    <div className="flex h-dvh flex-col" dir={dir} lang={locale}>
      <div className="flex min-h-0 flex-1">
        <NavRail />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar nightWindow={statusState.status?.pipeline.night_window ?? false} />
          <main className="min-h-0 flex-1 overflow-y-auto bg-bg">
            <ErrorBoundary key={location.pathname}>
              <Outlet context={statusState} />
            </ErrorBoundary>
          </main>
        </div>
        <ChatPanel />
      </div>
      <StatusStrip state={statusState} />
      <CommandPalette />
    </div>
  );
}
