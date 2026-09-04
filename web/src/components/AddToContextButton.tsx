import { Plus, Check } from "lucide-react";
import { useUiStore } from "@/store/uiStore";
import { cn } from "@/lib/cn";

export function AddToContextButton({
  kind,
  id,
  label,
  size = "md",
}: {
  kind: "item" | "entity";
  id: number;
  label: string;
  size?: "sm" | "md";
}) {
  const chatContext = useUiStore((s) => s.chatContext);
  const addToChatContext = useUiStore((s) => s.addToChatContext);
  const setChatOpen = useUiStore((s) => s.setChatOpen);
  const added = chatContext.some((c) => c.kind === kind && c.id === id);

  return (
    <button
      type="button"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/x-eo-context", JSON.stringify({ kind, id, label }));
      }}
      onClick={(e) => {
        e.stopPropagation();
        addToChatContext({ kind, id, label });
        setChatOpen(true);
      }}
      disabled={added}
      className={cn(
        "flex items-center gap-1 rounded-md border border-border-strong text-fg-muted hover:bg-bg-sunken hover:text-fg disabled:opacity-60",
        size === "sm" ? "px-1.5 py-0.5 text-xs" : "px-2 py-1 text-sm",
      )}
      title={added ? "כבר בהקשר" : "הוסף להקשר השיחה"}
    >
      {added ? (
        <Check size={size === "sm" ? 11 : 13} aria-hidden="true" />
      ) : (
        <Plus size={size === "sm" ? 11 : 13} aria-hidden="true" />
      )}
      <span>הוסף להקשר</span>
    </button>
  );
}
