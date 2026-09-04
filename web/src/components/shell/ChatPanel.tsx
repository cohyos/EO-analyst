import { MessageSquareText, PanelRightClose, X } from "lucide-react";
import { useAskChat } from "@/hooks/useAskChat";
import { ChatThread } from "@/components/ask/ChatThread";
import { useUiStore } from "@/store/uiStore";

export function ChatPanel() {
  const chatOpen = useUiStore((s) => s.chatOpen);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const chat = useAskChat();

  if (!chatOpen) {
    return (
      <button
        type="button"
        onClick={() => setChatOpen(true)}
        className="fixed bottom-14 start-4 z-30 flex items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg shadow-panel hover:opacity-90"
        aria-label="פתח את פאנל שאל את האנליסט"
      >
        <MessageSquareText size={16} aria-hidden="true" />
        שאל את האנליסט
      </button>
    );
  }

  return (
    <aside
      className="flex w-96 shrink-0 flex-col border-r border-border bg-bg-raised"
      aria-label="שאל את האנליסט"
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
