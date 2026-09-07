import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/api";
import type { GraphSearchResult } from "@/types/api";
import { entityKindLabel } from "@/components/entities/eventKindLabel";
import { countryFlagEmoji } from "@/lib/countryFlag";

/** Debounced autocomplete over `GET /api/graph/search` -- used both by the main "מרכז גרף
 * סביב..." box and the path-finder's two entity pickers. */
export function EntitySearchBox({
  placeholder,
  onSelect,
  autoFocus,
  "aria-label": ariaLabel,
}: {
  placeholder: string;
  onSelect: (entity: GraphSearchResult) => void;
  autoFocus?: boolean;
  "aria-label"?: string;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<GraphSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setResults([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .searchGraphEntities(query, 10)
        .then((r) => {
          if (!cancelled) {
            setResults(r);
            setOpen(true);
          }
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [q]);

  useEffect(() => {
    function onClickOutside(ev: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(ev.target as Node))
        setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <Search
        size={14}
        className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-fg-dim"
        style={{ insetInlineStart: "0.6rem" }}
        aria-hidden="true"
      />
      <input
        type="search"
        value={q}
        autoFocus={autoFocus}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        className="w-full rounded-md border border-border-strong bg-bg-raised py-1.5 text-sm text-fg placeholder:text-fg-dim"
        style={{ paddingInlineStart: "1.9rem", paddingInlineEnd: "0.6rem" }}
      />
      {open && (loading || results.length > 0) && (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border-strong bg-bg-raised shadow-panel">
          {loading && results.length === 0 && (
            <li className="px-2 py-1.5 text-xs text-fg-dim">מחפש…</li>
          )}
          {results.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => {
                  onSelect(r);
                  setQ("");
                  setResults([]);
                  setOpen(false);
                }}
                className="flex w-full items-center gap-1.5 px-2 py-1.5 text-start text-sm hover:bg-bg-sunken"
              >
                <bdi className="min-w-0 flex-1 truncate">{r.name}</bdi>
                {r.country && (
                  <span className="shrink-0 text-xs">{countryFlagEmoji(r.country)}</span>
                )}
                <span className="shrink-0 rounded bg-bg-sunken px-1 py-0.5 text-[10px] text-fg-dim">
                  {entityKindLabel(r.kind)}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-fg-dim">
                  {r.mention_count}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
