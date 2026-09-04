import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Square, X } from "lucide-react";
import type { ChatMessage } from "@/hooks/useAskChat";
import { CitationText } from "@/components/CitationText";
import { EmptyState } from "@/components/states";
import { useUiStore } from "@/store/uiStore";
import { cn } from "@/lib/cn";

export function ChatThread({
  messages,
  isStreaming,
  error,
  onSend,
  onStop,
  compact,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
  error: string | null;
  onSend: (question: string) => void;
  onStop: () => void;
  compact?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const navigate = useNavigate();
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
              <bdi className="max-w-[10rem] truncate">{c.label}</bdi>
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
                <CitationText
                  text={m.content || (m.streaming ? "…" : "")}
                  citations={m.citations}
                  onOpenItem={(id) => navigate(`/feed?open=${id}`)}
                />
              ) : (
                <bdi className="block">{m.content}</bdi>
              )}
              {m.streaming && (
                <span className="ms-1 inline-block h-3 w-1 animate-pulse bg-accent align-middle" />
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

      <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-border p-2">
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
        <SourcesList messages={messages} />
      )}
    </div>
  );
}

function SourcesList({ messages }: { messages: ChatMessage[] }) {
  const allCitations = messages.flatMap((m) => m.citations);
  const unique = Array.from(new Map(allCitations.map((c) => [c.item_id, c])).values());
  if (unique.length === 0) return null;
  return (
    <div className="border-t border-border p-3">
      <h3 className="mb-2 text-xs font-medium text-fg-dim">מקורות</h3>
      <ul className="space-y-1.5">
        {unique.map((c) => (
          <li key={c.item_id} className="text-xs">
            <a
              href={c.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-start gap-1.5 text-accent hover:underline"
            >
              <span className="font-mono text-fg-dim">[{c.n}]</span>
              <bdi className="line-clamp-2">{c.title}</bdi>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
