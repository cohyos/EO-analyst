import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Command, Languages, LockKeyhole, Moon, Search, Sun } from "lucide-react";
import { usePageTitle } from "./nav";
import { RunNowButton } from "./RunNowButton";
import { useUiStore } from "@/store/uiStore";
import { cn } from "@/lib/cn";
import { useI18n } from "@/i18n";
import { getRemoteSessionActive, logoutRemoteAccess, subscribeRemoteSession } from "@/api/real";

export function TopBar({ nightWindow }: { nightWindow: boolean }) {
  const location = useLocation();
  const title = usePageTitle(location.pathname);
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const { t, locale, toggleLocale } = useI18n();
  const queryClient = useQueryClient();

  // ADR-008 (docs/adr/008-remote-access.md): a small "remote session active" indicator + logout,
  // shown only for a non-loopback client that has actually logged in (the `X-EOA-Remote-Session`
  // response header -- see `auth.py::RemoteAccessMiddleware`). Never shown for ordinary local use.
  const [remoteSession, setRemoteSession] = useState(getRemoteSessionActive);
  useEffect(() => subscribeRemoteSession(setRemoteSession), []);

  async function handleRemoteLogout() {
    await logoutRemoteAccess();
    await queryClient.invalidateQueries();
  }

  return (
    <header className="pt-safe flex min-h-14 min-w-0 shrink-0 items-center gap-2 border-b border-border bg-bg-raised px-2 sm:gap-3 sm:px-4">
      {/* min-h (not h-14): pt-safe adds env(safe-area-inset-top) on top of the
          normal 56px bar height in iOS standalone mode (status bar overlaps
          content there) — a fixed height would squeeze the row's own content
          instead of growing the bar. */}
      <h1 className="min-w-0 shrink truncate text-base font-semibold">{title}</h1>

      <span
        className={cn(
          "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
          nightWindow
            ? "bg-accent-muted text-accent-fg"
            : "bg-bg-sunken text-fg-dim",
        )}
        title={nightWindow ? t("topBar.nightWindowActiveTitle") : t("topBar.nightWindowInactiveTitle")}
      >
        <span aria-hidden="true">{nightWindow ? "🌙" : "☀️"}</span>
        <span className="hidden sm:inline">
          {" "}
          {nightWindow ? t("topBar.nightWindowActive") : t("topBar.nightWindowInactive")}
        </span>
      </span>

      <div className="min-w-0 flex-1" />

      <button
        type="button"
        onClick={() => setCommandPaletteOpen(true)}
        className="tap-target flex shrink-0 items-center gap-2 rounded-md border border-border-strong p-1.5 text-sm text-fg-muted hover:bg-bg-sunken sm:px-3 sm:py-1.5"
        aria-label={t("common.searchGlobal")}
      >
        <Search size={16} aria-hidden="true" className="sm:hidden" />
        <span className="hidden sm:inline">{t("common.search")}</span>
        <span className="hidden items-center gap-0.5 rounded border border-border-strong bg-bg px-1 font-mono text-xs sm:flex">
          <Command size={11} aria-hidden="true" />K
        </span>
      </button>

      <RunNowButton />

      {remoteSession && (
        <button
          type="button"
          onClick={() => {
            void handleRemoteLogout();
          }}
          className="tap-target flex shrink-0 items-center gap-1 rounded-md border border-border-strong px-2 py-2 text-xs font-medium text-fg-muted hover:bg-bg-sunken"
          aria-label={t("topBar.remoteLogout")}
          title={t("topBar.remoteSessionActiveTitle")}
          data-testid="remote-logout"
        >
          <LockKeyhole size={16} aria-hidden="true" />
          <span className="hidden sm:inline">{t("topBar.remoteLogout")}</span>
        </button>
      )}

      <button
        type="button"
        onClick={toggleLocale}
        className="tap-target flex shrink-0 items-center gap-1 rounded-md border border-border-strong px-2 py-2 text-xs font-medium text-fg-muted hover:bg-bg-sunken"
        aria-label={t("topBar.languageToggleAriaLabel")}
        title={t("topBar.languageToggleAriaLabel")}
        data-testid="language-toggle"
        data-locale={locale}
      >
        <Languages size={16} aria-hidden="true" />
        <span aria-hidden="true">{t("topBar.languageToggleLabel")}</span>
      </button>

      <button
        type="button"
        onClick={toggleTheme}
        className="tap-target inline-flex shrink-0 items-center justify-center rounded-md border border-border-strong p-2 text-fg-muted hover:bg-bg-sunken"
        aria-label={theme === "dark" ? t("topBar.themeToLight") : t("topBar.themeToDark")}
      >
        {theme === "dark" ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
      </button>
    </header>
  );
}
