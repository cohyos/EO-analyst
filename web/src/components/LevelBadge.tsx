import { Archive, Eye, Flame, TriangleAlert } from "lucide-react";
import type { TriageLevel } from "@/types/api";
import { cn } from "@/lib/cn";

export const LEVEL_META: Record<
  TriageLevel,
  { label: string; icon: typeof Flame; fg: string; bg: string }
> = {
  red: { label: "קריטי", icon: Flame, fg: "text-level-red", bg: "bg-level-red-bg" },
  orange: {
    label: "חשוב",
    icon: TriangleAlert,
    fg: "text-level-orange",
    bg: "bg-level-orange-bg",
  },
  yellow: { label: "רקע", icon: Eye, fg: "text-level-yellow", bg: "bg-level-yellow-bg" },
  archive: {
    label: "ארכיון",
    icon: Archive,
    fg: "text-level-archive",
    bg: "bg-level-archive-bg",
  },
};

export function LevelBadge({
  level,
  size = "md",
}: {
  level: TriageLevel;
  size?: "sm" | "md";
}) {
  const meta = LEVEL_META[level];
  const Icon = meta.icon;
  return (
    <span
      role="img"
      aria-label={`רמת דחיפות: ${meta.label}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-md font-medium",
        meta.fg,
        meta.bg,
        size === "sm" ? "px-1.5 py-0.5 text-xs" : "px-2 py-1 text-sm",
      )}
      data-level={level}
    >
      <Icon aria-hidden="true" size={size === "sm" ? 12 : 14} />
      <span>{meta.label}</span>
    </span>
  );
}
