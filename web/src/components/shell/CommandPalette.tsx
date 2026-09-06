import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useUiStore } from "@/store/uiStore";
import { api } from "@/api";
import { LevelBadge } from "@/components/LevelBadge";

export function CommandPalette() {
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!open);
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  const itemsQuery = useQuery({
    queryKey: ["command-palette-items", q],
    queryFn: () => api.getItems({ q, page_size: 6, sort: "score" }),
    enabled: open && q.length > 1,
  });
  const entitiesQuery = useQuery({
    queryKey: ["command-palette-entities", q],
    queryFn: () => api.getEntities({ q, limit: 6 }),
    enabled: open && q.length > 1,
  });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[max(1.5rem,env(safe-area-inset-top))] sm:pt-24"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg rounded-lg border border-border-strong bg-bg-raised shadow-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="חיפוש גלובלי"
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <Search size={16} className="text-fg-dim" aria-hidden="true" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="חפש פריטים, ישויות…"
            className="flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-dim"
          />
          <kbd className="rounded border border-border-strong px-1.5 py-0.5 text-xs text-fg-dim">
            Esc
          </kbd>
        </div>
        <div className="max-h-96 overflow-y-auto p-2">
          {q.length <= 1 && (
            <p className="p-3 text-sm text-fg-dim">הקלד לפחות 2 תווים לחיפוש</p>
          )}
          {itemsQuery.data && itemsQuery.data.items.length > 0 && (
            <div className="mb-2">
              <p className="px-2 py-1 text-xs font-medium text-fg-dim">פריטים</p>
              {itemsQuery.data.items.map((it) => (
                <button
                  key={it.id}
                  type="button"
                  onClick={() => {
                    navigate(`/feed?open=${it.id}`);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm hover:bg-bg-sunken"
                >
                  <LevelBadge level={it.level} size="sm" />
                  <bdi className="truncate">{it.title}</bdi>
                </button>
              ))}
            </div>
          )}
          {entitiesQuery.data && entitiesQuery.data.length > 0 && (
            <div>
              <p className="px-2 py-1 text-xs font-medium text-fg-dim">ישויות</p>
              {entitiesQuery.data.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  onClick={() => {
                    navigate(`/entities/${e.id}`);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-start text-sm hover:bg-bg-sunken"
                >
                  <bdi className="truncate">{e.name}</bdi>
                  <span className="text-xs text-fg-dim">{e.kind}</span>
                </button>
              ))}
            </div>
          )}
          {q.length > 1 &&
            itemsQuery.data?.items.length === 0 &&
            entitiesQuery.data?.length === 0 && (
              <p className="p-3 text-sm text-fg-dim">אין תוצאות</p>
            )}
        </div>
      </div>
    </div>
  );
}
