import { useState } from "react";
import { MessageSquareText, PanelRightClose, X } from "lucide-react";
import { useAskChat } from "@/hooks/useAskChat";
import { ChatThread } from "@/components/ask/ChatThread";
import { useUiStore, type ChatContextItem } from "@/store/uiStore";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";

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
  const t = useT();
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
          // Between `md` and `xl` the nav rail (NavRail.tsx) is a `w-16`
          // (64px) icon column docked at the *start* edge (right in
          // RTL/Hebrew, left in LTR) — `start-4` alone sat inside that
          // column, overlapping its icons on short/narrow viewports.
          // `md:start-20` (80px = 64px rail + 16px clearance) keeps the FAB
          // clear of it. Moving to the opposite (`end`) edge instead isn't
          // an option: the full /ask page's own composer send button
          // (ChatThread.tsx) lives right there, at the bottom of the *end*
          // edge, which this FAB would then cover on every screen. `xl:start-4`
          // reverts to the original tighter inset once the rail widens to
          // `xl:w-48` but desktop viewports are tall enough that the rail's
          // icons no longer reach this corner. Below `md:` there is no rail
          // at all (MobileNav's bottom tab bar replaces it), so `start-4`.
          //
          // Bottom offset at `md:` and up is untouched (`3.5rem`, exactly as
          // before — desktop must stay pixel-identical). Below `md:` it uses
          // `--eoa-statusbar-clear-h` instead of the bare `3.5rem`: that
          // constant alone under-cleared StatusStrip once its
          // "local inference paused" banner is showing (the FAB's bottom
          // edge landed inside the banner's text), and also adds
          // `--eoa-tabbar-h` to clear MobileNav's fixed bottom tab bar,
          // which only exists below `md:` (both vars set on AppShell's root).
          "fixed z-30 flex items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg shadow-panel hover:opacity-90",
          "bottom-[calc(var(--eoa-statusbar-clear-h)+env(safe-area-inset-bottom)+var(--eoa-tabbar-h))] start-4",
          "md:bottom-[calc(3.5rem+env(safe-area-inset-bottom))] md:start-20 xl:start-4",
          dragOver && "ring-2 ring-accent-fg ring-offset-2 ring-offset-bg",
        )}
        aria-label={t("shell.chatFabAria")}
      >
        <MessageSquareText size={16} aria-hidden="true" />
        {t("shell.chatFabLabel")}
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
      aria-label={t("shell.chatPanelAria")}
    >
      <div className="pt-safe flex min-h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <MessageSquareText size={16} className="text-accent" aria-hidden="true" />
        <h2 className="text-sm font-semibold">{t("shell.chatPanelTitle")}</h2>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setChatOpen(false)}
          className="tap-target inline-flex items-center justify-center rounded p-1 text-fg-muted hover:bg-bg-sunken hover:text-fg"
          aria-label={t("shell.collapsePanelAria")}
        >
          <PanelRightClose size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setChatOpen(false)}
          className="tap-target inline-flex items-center justify-center rounded p-1 text-fg-muted hover:bg-bg-sunken hover:text-fg md:hidden"
          aria-label={t("shell.closeAria")}
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
          provider={chat.provider}
          onProviderChange={chat.setProvider}
          compact
        />
      </div>
    </aside>
  );
}
