import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Locale } from "@/i18n/types";

export type Theme = "dark" | "light";

export interface ChatContextItem {
  kind: "item" | "entity";
  id: number;
  label: string;
}

interface UiState {
  theme: Theme;
  toggleTheme: () => void;

  locale: Locale;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;

  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
  toggleChat: () => void;

  chatContext: ChatContextItem[];
  addToChatContext: (entry: ChatContextItem) => void;
  removeFromChatContext: (kind: ChatContextItem["kind"], id: number) => void;
  clearChatContext: () => void;

  commandPaletteOpen: boolean;
  setCommandPaletteOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      toggleTheme: () => set({ theme: get().theme === "dark" ? "light" : "dark" }),

      // Default remains Hebrew (U6) — never inferred from browser locale, so
      // every session opens in the language the product is built around.
      locale: "he",
      setLocale: (locale) => set({ locale }),
      toggleLocale: () => set({ locale: get().locale === "he" ? "en" : "he" }),

      chatOpen: false,
      setChatOpen: (open) => set({ chatOpen: open }),
      toggleChat: () => set({ chatOpen: !get().chatOpen }),

      chatContext: [],
      addToChatContext: (entry) => {
        const exists = get().chatContext.some(
          (c) => c.kind === entry.kind && c.id === entry.id,
        );
        if (exists) return;
        set({ chatContext: [...get().chatContext, entry] });
      },
      removeFromChatContext: (kind, id) =>
        set({
          chatContext: get().chatContext.filter((c) => !(c.kind === kind && c.id === id)),
        }),
      clearChatContext: () => set({ chatContext: [] }),

      commandPaletteOpen: false,
      setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
    }),
    {
      name: "eo-analyst-ui",
      partialize: (state) => ({ theme: state.theme, locale: state.locale }),
    },
  ),
);
