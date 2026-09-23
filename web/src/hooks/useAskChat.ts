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

// iPhone/WebKit chat "Load failed" investigation (2026-09-15): `/api/ask` streams tokens, then
// runs several silent server-side guard passes (citation repair, entailment check up to ~60s,
// anchor/coherence checks) before the final `answer_final`/`sources`/`done` SSE events -- a gap
// that can exceed WebKit's ~60s per-request idle-network timeout even though the request never
// had an AbortController deadline on the client (`api/real.ts`'s `askStream` only aborts on the
// user's own "עצור" button). Safari then throws a bare `TypeError: Load failed`/"cancelled" with
// no detail, which used to reach the user verbatim (see `ChatThread.tsx`'s `{error}` render). This
// maps the small, stable set of generic browser network-failure messages (WebKit's "Load failed"/
// "cancelled", Chromium's "Failed to fetch", Firefox's "NetworkError...") to a clear Hebrew
// explanation instead -- the partial answer already streamed via `onToken` is left untouched (see
// `send`'s `onError` below), only the error line changes. The real fix is server-side (a heartbeat
// during the silent guard-pass gap, see `agent/eoa/api/routes/ask.py`) and needs an API restart;
// this is the client-side mitigation that ships without one.
const _GENERIC_NETWORK_FAILURE_RE =
  /^(load failed|cancelled|failed to fetch|networkerror when attempting to fetch resource\.?|the network connection was lost\.?|network request failed)$/i;

export function friendlyAskErrorMessage(err: Pick<Error, "message">): string {
  const msg = (err.message ?? "").trim();
  if (_GENERIC_NETWORK_FAILURE_RE.test(msg)) {
    return (
      "החיבור לשרת נקטע באמצע קבלת התשובה (ייתכן עקב זמן עיבוד ארוך ברשת הנייד). " +
      "התשובה החלקית שכבר התקבלה מוצגת למעלה — אפשר לנסות לשלוח את השאלה שוב."
    );
  }
  return msg || "שגיאה בתקשורת עם השרת";
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
          // Round 2 (docs/qa/loop/round_2_chat_fixes.md): server-side citation repair / no-
          // citations / off-topic-anchor guard wholesale-replaces the answer — overwrite, don't
          // append, so the corrected text (or warning-prefixed text) is what's shown.
          onAnswerFinal: (text) => {
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, content: text } : m)),
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
            setError(friendlyAskErrorMessage(err));
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
            );
          },
          // F30 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `stop()` below already flips
          // the hook's own `isStreaming`, but the per-message `streaming` flag (the bubble's
          // "still typing" indicator) was previously only ever cleared by `onDone`/`onError` --
          // neither of which used to fire on a user-initiated abort, so the last assistant bubble
          // stayed stuck mid-stream forever. No error text: this isn't a failure.
          onAbort: () => {
            setIsStreaming(false);
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
