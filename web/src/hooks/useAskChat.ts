import { useCallback, useRef, useState } from "react";
import type { AskCitation, AskHistoryMessage } from "@/types/api";
import { api } from "@/api";
import { useUiStore } from "@/store/uiStore";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: AskCitation[];
  streaming?: boolean;
}

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `msg-${idCounter}`;
}

export function useAskChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const chatContext = useUiStore((s) => s.chatContext);

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
      };
      const assistantId = nextId();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: [],
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
    [messages, isStreaming, chatContext],
  );

  const stop = useCallback(() => {
    abortRef.current?.();
    setIsStreaming(false);
  }, []);

  const reset = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return { messages, isStreaming, error, send, stop, reset };
}
