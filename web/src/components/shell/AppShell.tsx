import { Outlet } from "react-router-dom";
import { useEffect } from "react";
import { NavRail } from "./NavRail";
import { TopBar } from "./TopBar";
import { StatusStrip } from "./StatusStrip";
import { ChatPanel } from "./ChatPanel";
import { CommandPalette } from "./CommandPalette";
import { useStatusSocket } from "@/hooks/useStatusSocket";
import { useUiStore } from "@/store/uiStore";

export function AppShell() {
  const statusState = useStatusSocket();
  const theme = useUiStore((s) => s.theme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className="flex h-screen flex-col" dir="rtl" lang="he">
      <div className="flex min-h-0 flex-1">
        <NavRail />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar nightWindow={statusState.status?.pipeline.night_window ?? false} />
          <main className="min-h-0 flex-1 overflow-y-auto bg-bg">
            <Outlet />
          </main>
        </div>
        <ChatPanel />
      </div>
      <StatusStrip state={statusState} />
      <CommandPalette />
    </div>
  );
}
