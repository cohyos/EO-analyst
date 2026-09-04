import { useLocation } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Command, Moon, Play, Sun } from "lucide-react";
import { useState } from "react";
import { pageTitleFor } from "./nav";
import { useUiStore } from "@/store/uiStore";
import { api } from "@/api";
import { cn } from "@/lib/cn";

export function TopBar({ nightWindow }: { nightWindow: boolean }) {
  const location = useLocation();
  const title = pageTitleFor(location.pathname);
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
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
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-bg-raised px-4">
      <h1 className="truncate text-base font-semibold">{title}</h1>

      <span
        className={cn(
          "rounded-full px-2 py-0.5 text-xs font-medium",
          nightWindow
            ? "bg-accent-muted text-accent-fg"
            : "bg-bg-sunken text-fg-dim",
        )}
        title={nightWindow ? "בתוך חלון הלילה הפעיל" : "מחוץ לחלון הלילה"}
      >
        {nightWindow ? "🌙 חלון לילה פעיל" : "☀️ מחוץ לחלון לילה"}
      </span>

      <div className="flex-1" />

      <button
        type="button"
        onClick={() => setCommandPaletteOpen(true)}
        className="flex items-center gap-2 rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg-muted hover:bg-bg-sunken"
        aria-label="חיפוש גלובלי"
      >
        <span>חיפוש</span>
        <span className="flex items-center gap-0.5 rounded border border-border-strong bg-bg px-1 font-mono text-xs">
          <Command size={11} aria-hidden="true" />K
        </span>
      </button>

      <button
        type="button"
        onClick={() => runNow.mutate()}
        disabled={runNow.isPending}
        className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
      >
        <Play size={14} aria-hidden="true" />
        {runNow.isPending ? "מריץ…" : justRan ? "הופעל ✓" : "הרץ עכשיו"}
      </button>

      <button
        type="button"
        onClick={toggleTheme}
        className="rounded-md border border-border-strong p-2 text-fg-muted hover:bg-bg-sunken"
        aria-label={theme === "dark" ? "עבור לערכת נושא בהירה" : "עבור לערכת נושא כהה"}
      >
        {theme === "dark" ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
      </button>
    </header>
  );
}
