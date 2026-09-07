import { useState, type FormEvent } from "react";
import { Cloud, Send, Square, X } from "lucide-react";
import type { ChatMessage } from "@/hooks/useAskChat";
import { AskAnswer } from "./AskAnswer";
import { AskSourcesFooter } from "./AskSourcesFooter";
import { EmptyState } from "@/components/states";
import { useUiStore } from "@/store/uiStore";
import { cn } from "@/lib/cn";
import { ModelPicker } from "./ModelPicker";
import type { AskCitation } from "@/types/api";

export function ChatThread({
  messages,
  isStreaming,
  error,
  onSend,
  onStop,
  compact,
  provider = null,
  onProviderChange,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
  error: string | null;
  onSend: (question: string) => void;
  onStop: () => void;
  compact?: boolean;
  /** U8: selected provider ("" / null = server default); omit both props to hide the picker. */
  provider?: string | null;
  onProviderChange?: (next: string | null) => void;
}) {
  const [draft, setDraft] = useState("");
  const chatContext = useUiStore((s) => s.chatContext);
  const removeFromChatContext = useUiStore((s) => s.removeFromChatContext);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    onSend(draft.trim());
    setDraft("");
  }

  return (
    <div className="flex h-full flex-col">
      {chatContext.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border p-2">
          {chatContext.map((c) => (
            <span
              key={`${c.kind}-${c.id}`}
              className="flex items-center gap-1 rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted"
            >
              <bdi className="max-w-[10rem] truncate" title={c.label}>
                {c.label}
              </bdi>
              <button
                type="button"
                aria-label={`הסר את ${c.label} מההקשר`}
                onClick={() => removeFromChatContext(c.kind, c.id)}
                className="text-fg-dim hover:text-danger"
              >
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 ? (
          <EmptyState
            title="שאל את האנליסט"
            description='שאלה פתוחה על ישויות, מגמות או פריטים — ניתן לצרף הקשר עם "הוסף להקשר".'
          />
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={cn(
                "max-w-[95%] rounded-lg px-3 py-2 text-sm leading-relaxed",
                m.role === "user"
                  ? "mr-0 ml-auto bg-accent-muted text-accent-fg"
                  : "bg-bg-raised text-fg shadow-panel",
              )}
            >
              {m.role === "assistant" ? (
                m.content ? (
                  <AskAnswer text={m.content} citations={m.citations} />
                ) : m.streaming ? (
                  <span className="text-fg-dim">…</span>
                ) : null
              ) : (
                <bdi className="block">{m.content}</bdi>
              )}
              {m.streaming && (
                <span className="ms-1 inline-block h-3 w-1 animate-pulse bg-accent align-middle" />
              )}
              {m.role === "assistant" && m.provider && (m.content || !m.streaming) && (
                <div className="mt-1.5 flex items-center gap-1 text-[10px] text-fg-dim">
                  {m.provider !== "ollama" && <Cloud size={10} aria-hidden="true" />}
                  <span>
                    {m.provider === "ollama" ? "מקומי" : "ענן"}
                    {m.providerModel ? ` · ${m.providerModel}` : ""}
                  </span>
                </div>
              )}
            </div>
          ))
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </div>

      {onProviderChange && (
        <div className="flex items-center justify-end border-t border-border px-2 pt-2">
          <ModelPicker value={provider} onChange={onProviderChange} compact={compact} />
        </div>
      )}
      <form onSubmit={handleSubmit} className={cn("flex items-center gap-2 p-2", !onProviderChange && "border-t border-border")}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="שאל שאלה…"
          className="flex-1 rounded-md border border-border-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-fg-dim focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          aria-label="שאלה לאנליסט"
        />
        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            className="rounded-md bg-danger px-3 py-2 text-sm text-white hover:opacity-90"
            aria-label="עצור"
          >
            <Square size={14} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!draft.trim()}
            className="rounded-md bg-accent px-3 py-2 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50"
            aria-label="שלח"
          >
            <Send size={14} aria-hidden="true" />
          </button>
        )}
      </form>
      {!compact && messages.some((m) => m.citations.length > 0) && (
        <AskSourcesFooter sources={mergeThreadSources(messages)} />
      )}
    </div>
  );
}

/** De-dupes by item_id across every message in the thread (a citation's `n` is only unique
 * within its own message, since each answer's [n] numbering restarts at 1), preferring the
 * level/source_name/note-enriched `sources` event over the earlier plain `citations` one. */
function mergeThreadSources(messages: ChatMessage[]): AskCitation[] {
  const all = messages.flatMap((m) => (m.sources.length > 0 ? m.sources : m.citations));
  return Array.from(new Map(all.map((c) => [c.item_id, c])).values());
}
