import { useState } from "react";
import { MessageSquareText, PanelRightClose, X } from "lucide-react";
import { useAskChat } from "@/hooks/useAskChat";
import { ChatThread } from "@/components/ask/ChatThread";
import { useUiStore, type ChatContextItem } from "@/store/uiStore";
import { cn } from "@/lib/cn";

function parseDropPayload(dt: DataTransfer): ChatContextItem | null {
  try {
    const raw = dt.getData("application/x-eo-context");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ChatContextItem>;
    if ((parsed.kind !== "item" && parsed.kind !== "entity") || typeof parsed.id !== "number") {
      return null;
    }
    return { kind: parsed.kind, id: parsed.id, label: String(parsed.label ?? "") };
  } catch {
    return null;
  }
}

export function ChatPanel() {
  const chatOpen = useUiStore((s) => s.chatOpen);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const addToChatContext = useUiStore((s) => s.addToChatContext);
  const chat = useAskChat();
  const [dragOver, setDragOver] = useState(false);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const entry = parseDropPayload(e.dataTransfer);
    if (entry) {
      addToChatContext(entry);
      setChatOpen(true);
    }
  }

  if (!chatOpen) {
    return (
      <button
        type="button"
        onClick={() => setChatOpen(true)}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        data-testid="chat-panel-fab-dropzone"
        className={cn(
          "fixed bottom-14 start-4 z-30 flex items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg shadow-panel hover:opacity-90",
          dragOver && "ring-2 ring-accent-fg ring-offset-2 ring-offset-bg",
        )}
        aria-label="פתח את פאנל שאל את האנליסט — גרור לכאן פריט או ישות כדי להוסיף להקשר"
      >
        <MessageSquareText size={16} aria-hidden="true" />
        שאל את האנליסט
      </button>
    );
  }

  return (
    <aside
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      data-testid="chat-panel-dropzone"
      className={cn(
        "fixed inset-0 z-40 flex w-full shrink-0 flex-col border-border bg-bg-raised sm:static sm:inset-auto sm:z-auto sm:w-96 sm:border-r",
        dragOver && "outline outline-2 -outline-offset-2 outline-accent",
      )}
      aria-label="שאל את האנליסט — גרור לכאן פריט או ישות כדי להוסיף להקשר"
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <MessageSquareText size={16} className="text-accent" aria-hidden="true" />
        <h2 className="text-sm font-semibold">שאל את האנליסט</h2>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setChatOpen(false)}
          className="rounded p-1 text-fg-muted hover:bg-bg-sunken hover:text-fg"
          aria-label="כווץ פאנל"
        >
          <PanelRightClose size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setChatOpen(false)}
          className="rounded p-1 text-fg-muted hover:bg-bg-sunken hover:text-fg md:hidden"
          aria-label="סגור"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <ChatThread
          messages={chat.messages}
          isStreaming={chat.isStreaming}
          error={chat.error}
          onSend={chat.send}
          onStop={chat.stop}
          compact
        />
      </div>
    </aside>
  );
}
