import { useCallback, useRef, useState } from "react";
import type { AskCitation, AskHistoryMessage } from "@/types/api";
import { api } from "@/api";
import { useUiStore } from "@/store/uiStore";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: AskCitation[];
  /** U11: citations enriched with level/source_name/note for the sources footer -- arrives once,
   * after the answer finishes streaming. Falls back to `citations` until then. */
  sources: AskCitation[];
  streaming?: boolean;
  /** U8: provider/model that answered this message (assistant messages only). */
  provider?: string;
  providerModel?: string;
}

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `msg-${idCounter}`;
}

const PROVIDER_STORAGE_KEY = "eoa.chat.provider";

function loadStoredProvider(): string | null {
  try {
    return localStorage.getItem(PROVIDER_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function useAskChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // U8: null means "use the server default" (llm_providers.interactive_default) — the picker
  // shows that as its own option rather than guessing a value here.
  const [provider, setProviderState] = useState<string | null>(loadStoredProvider);
  const abortRef = useRef<(() => void) | null>(null);
  const chatContext = useUiStore((s) => s.chatContext);

  const setProvider = useCallback((next: string | null) => {
    setProviderState(next);
    try {
      if (next) localStorage.setItem(PROVIDER_STORAGE_KEY, next);
      else localStorage.removeItem(PROVIDER_STORAGE_KEY);
    } catch {
      // best-effort — a private/blocked storage just means the choice isn't remembered
    }
  }, []);

  const send = useCallback(
    (question: string) => {
      if (!question.trim() || isStreaming) return;
      setError(null);
      const history: AskHistoryMessage[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));
      const userMsg: ChatMessage = {
        id: nextId(),
        role: "user",
        content: question,
        citations: [],
        sources: [],
      };
      const assistantId = nextId();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: [],
        sources: [],
        streaming: true,
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);

      const contextItemIds = chatContext
        .filter((c) => c.kind === "item")
        .map((c) => c.id);
      const contextEntityIds = chatContext
        .filter((c) => c.kind === "entity")
        .map((c) => c.id);

      const abort = api.askStream(
        {
          question,
          context_item_ids: contextItemIds,
          context_entity_ids: contextEntityIds,
          history,
          provider,
        },
        {
          onToken: (text) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + text } : m,
              ),
            );
          },
          onCitations: (items) => {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, citations: items } : m)),
            );
          },
          onSources: (items) => {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, sources: items } : m)),
            );
          },
          onMeta: (providerKind, providerModel) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, provider: providerKind, providerModel } : m,
              ),
            );
          },
          onDone: () => {
            setIsStreaming(false);
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
            );
          },
          onError: (err) => {
            setIsStreaming(false);
            setError(err.message || "שגיאה בתקשורת עם השרת");
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
            );
          },
        },
      );
      abortRef.current = abort;
    },
    [messages, isStreaming, chatContext, provider],
  );

  const stop = useCallback(() => {
    abortRef.current?.();
    setIsStreaming(false);
  }, []);

  const reset = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return { messages, isStreaming, error, send, stop, reset, provider, setProvider };
}
