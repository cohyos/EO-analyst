import { useState } from "react";
import { MessageSquareText, PanelRightClose, X } from "lucide-react";
import { useAskChat } from "@/hooks/useAskChat";
import { useMainScrollDirection } from "@/hooks/useMainScrollDirection";
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
  // Round-3 mobile fix (UI-MOBILE-iphone-r3.md #1): the phone-only compact trigger hides itself
  // while the analyst is actively scrolling `<main>` down (it otherwise sits fixed over content),
  // and reappears on scroll-up or after a short idle pause.
  const scrollHiddenOnPhone = useMainScrollDirection();

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(true);
  }

  function handleDragLeave() {
    setDragOver(false);
  }

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
      <>
        {/* Below `md:`: a compact icon-only round button (56x56) instead of the full labelled
            pill -- the pill's visible text plus its fixed positioning was wide enough to sit over
            feed/report content on a 390px screen. Icon + aria-label only, same drag-to-add-context
            affordance as the desktop pill. */}
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          data-testid="chat-panel-fab-compact"
          className={cn(
            "fixed z-30 flex h-14 w-14 items-center justify-center rounded-full bg-accent text-accent-fg shadow-panel hover:opacity-90 md:hidden",
            "bottom-[calc(var(--eoa-statusbar-clear-h)+env(safe-area-inset-bottom)+var(--eoa-tabbar-h))] start-4",
            "transition-transform duration-200 ease-out",
            // Translating by just the button's own height wasn't enough to actually clear the
            // viewport: this button's resting `bottom` offset is itself
            // `--eoa-statusbar-clear-h + --eoa-tabbar-h` (well over 100px, to sit above
            // StatusStrip/MobileNav) — a `translate-y-[calc(100%+1rem)]` verified via Playwright
            // against the real app left it still fully on-screen, just shifted down slightly.
            // Cancelling that same bottom-offset expression here on top of `100%` is what actually
            // pushes the button's top edge below the viewport's bottom edge.
            scrollHiddenOnPhone
              ? "translate-y-[calc(100%+var(--eoa-statusbar-clear-h)+env(safe-area-inset-bottom)+var(--eoa-tabbar-h)+1rem)]"
              : "translate-y-0",
            dragOver && "ring-2 ring-accent-fg ring-offset-2 ring-offset-bg",
          )}
          aria-label={t("shell.chatFabLabel")}
        >
          <MessageSquareText size={20} aria-hidden="true" />
        </button>

        {/* `md:`+: the original labelled pill, pixel-identical to before this round. */}
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
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
            // icons no longer reach this corner.
            //
            // Bottom offset at `md:` and up is untouched (`3.5rem`, exactly as
            // before — desktop must stay pixel-identical).
            "fixed z-30 hidden items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg shadow-panel hover:opacity-90 md:flex",
            "md:bottom-[calc(3.5rem+env(safe-area-inset-bottom))] md:start-20 xl:start-4",
            dragOver && "ring-2 ring-accent-fg ring-offset-2 ring-offset-bg",
          )}
          aria-label={t("shell.chatFabAria")}
        >
          <MessageSquareText size={16} aria-hidden="true" />
          {t("shell.chatFabLabel")}
        </button>
      </>
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
