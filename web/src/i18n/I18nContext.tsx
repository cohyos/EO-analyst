import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useUiStore } from "@/store/uiStore";
import { he } from "./dictionaries/he";
import { en } from "./dictionaries/en";
import { translate } from "./resolve";
import type { Locale, TranslationKey, TranslationParams } from "./types";

const DICTIONARIES = { he, en };

interface I18nContextValue {
  locale: Locale;
  dir: "rtl" | "ltr";
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (key: TranslationKey, params?: TranslationParams) => string;
}

// Falls back to a static Hebrew translator when no provider is mounted
// (component/unit tests that render e.g. `<LevelBadge>` or `<TopBar>` in
// isolation, without wrapping `<I18nProvider>`) rather than throwing —
// dozens of existing tests across pages this task doesn't own render shared
// components directly, and the default locale is Hebrew anyway, so the
// fallback renders identically to what a provider would produce for `he`.
const DEFAULT_CONTEXT: I18nContextValue = {
  locale: "he",
  dir: "rtl",
  setLocale: () => {},
  toggleLocale: () => {},
  t: (key, params) => translate(DICTIONARIES.he, key, params),
};

const I18nContext = createContext<I18nContextValue>(DEFAULT_CONTEXT);

/**
 * Provides the current locale + a typed `t(key)` translator (U6). Locale
 * lives in the persisted `uiStore` (same `localStorage` mechanism as the
 * theme toggle) so this provider is just a thin reactive layer on top —
 * mount it once near the app root (see `App.tsx`).
 *
 * Also keeps `<html dir lang>` in sync with the active locale so RTL/LTR
 * mirroring (Tailwind logical utilities, see docs/MODULES.md "Web UI") and
 * screen-reader language apply globally, not just inside the React tree.
 */
export function I18nProvider({ children }: { children: ReactNode }) {
  const locale = useUiStore((s) => s.locale);
  const setLocale = useUiStore((s) => s.setLocale);
  const toggleLocale = useUiStore((s) => s.toggleLocale);
  const dir = locale === "he" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.setAttribute("lang", locale);
    document.documentElement.setAttribute("dir", dir);
  }, [locale, dir]);

  const t = useCallback(
    (key: TranslationKey, params?: TranslationParams) => translate(DICTIONARIES[locale], key, params),
    [locale],
  );

  const value = useMemo<I18nContextValue>(
    () => ({ locale, dir, setLocale, toggleLocale, t }),
    [locale, dir, setLocale, toggleLocale, t],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

/** Convenience hook for components that only need the translator. */
export function useT() {
  return useI18n().t;
}
