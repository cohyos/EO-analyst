import { useLocation } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Command, Languages, Moon, Play, Search, Sun } from "lucide-react";
import { useState } from "react";
import { usePageTitle } from "./nav";
import { useUiStore } from "@/store/uiStore";
import { api } from "@/api";
import { cn } from "@/lib/cn";
import { useI18n } from "@/i18n";

export function TopBar({ nightWindow }: { nightWindow: boolean }) {
  const location = useLocation();
  const title = usePageTitle(location.pathname);
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const { t, locale, toggleLocale } = useI18n();
  const queryClient = useQueryClient();
  const [justRan, setJustRan] = useState(false);

  const runNow = useMutation({
    mutationFn: () => api.postRun("daily", "full"),
    onSuccess: () => {
      setJustRan(true);
      queryClient.invalidateQueries({ queryKey: ["status"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setTimeout(() => setJustRan(false), 2500);
    },
  });

  return (
    <header className="flex h-14 min-w-0 shrink-0 items-center gap-2 border-b border-border bg-bg-raised px-2 sm:gap-3 sm:px-4">
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
        className="flex shrink-0 items-center gap-2 rounded-md border border-border-strong p-1.5 text-sm text-fg-muted hover:bg-bg-sunken sm:px-3 sm:py-1.5"
        aria-label={t("common.searchGlobal")}
      >
        <Search size={16} aria-hidden="true" className="sm:hidden" />
        <span className="hidden sm:inline">{t("common.search")}</span>
        <span className="hidden items-center gap-0.5 rounded border border-border-strong bg-bg px-1 font-mono text-xs sm:flex">
          <Command size={11} aria-hidden="true" />K
        </span>
      </button>

      <button
        type="button"
        onClick={() => runNow.mutate()}
        disabled={runNow.isPending}
        aria-label={runNow.isPending ? t("topBar.runningAriaLabel") : justRan ? t("topBar.ranSuccessfullyAriaLabel") : t("topBar.runNow")}
        className="flex shrink-0 items-center gap-1.5 rounded-md bg-accent px-2 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60 sm:px-3"
      >
        <Play size={14} aria-hidden="true" />
        <span className="hidden sm:inline">
          {runNow.isPending ? t("topBar.running") : justRan ? t("topBar.ranSuccessfully") : t("topBar.runNow")}
        </span>
      </button>

      <button
        type="button"
        onClick={toggleLocale}
        className="flex shrink-0 items-center gap-1 rounded-md border border-border-strong px-2 py-2 text-xs font-medium text-fg-muted hover:bg-bg-sunken"
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
        className="shrink-0 rounded-md border border-border-strong p-2 text-fg-muted hover:bg-bg-sunken"
        aria-label={theme === "dark" ? t("topBar.themeToLight") : t("topBar.themeToDark")}
      >
        {theme === "dark" ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
      </button>
    </header>
  );
}
