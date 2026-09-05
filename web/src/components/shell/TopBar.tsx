import { useLocation } from "react-router-dom";
import { Command, Languages, Moon, Search, Sun } from "lucide-react";
import { usePageTitle } from "./nav";
import { RunNowButton } from "./RunNowButton";
import { useUiStore } from "@/store/uiStore";
import { cn } from "@/lib/cn";
import { useI18n } from "@/i18n";

export function TopBar({ nightWindow }: { nightWindow: boolean }) {
  const location = useLocation();
  const title = usePageTitle(location.pathname);
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const { t, locale, toggleLocale } = useI18n();

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

      <RunNowButton />

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
