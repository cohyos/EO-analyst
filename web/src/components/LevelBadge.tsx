import { Archive, CircleDashed, Eye, Flame, TriangleAlert } from "lucide-react";
import type { TriageLevel } from "@/types/api";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

// Colors/icons stay static (not locale-dependent); the visible label is
// resolved through `t()` (U6) via `useLevelLabel()`/`LevelBadge` below, from
// the `levels.*` dictionary keys — kept in sync with these ids.
export const LEVEL_META: Record<
  TriageLevel,
  { labelKey: TranslationKey; icon: typeof Flame; fg: string; bg: string }
> = {
  red: { labelKey: "levels.red", icon: Flame, fg: "text-level-red", bg: "bg-level-red-bg" },
  orange: {
    labelKey: "levels.orange",
    icon: TriangleAlert,
    fg: "text-level-orange",
    bg: "bg-level-orange-bg",
  },
  yellow: { labelKey: "levels.yellow", icon: Eye, fg: "text-level-yellow", bg: "bg-level-yellow-bg" },
  archive: {
    labelKey: "levels.archive",
    icon: Archive,
    fg: "text-level-archive",
    bg: "bg-level-archive-bg",
  },
  unclassified: {
    labelKey: "levels.unclassified",
    icon: CircleDashed,
    fg: "text-level-unclassified",
    bg: "bg-level-unclassified-bg",
  },
};

/** Localized display label for a triage level (U6). */
export function useLevelLabel(level: TriageLevel): string {
  return useT()(LEVEL_META[level].labelKey);
}

export function LevelBadge({
  level,
  size = "md",
}: {
  level: TriageLevel;
  size?: "sm" | "md";
}) {
  const meta = LEVEL_META[level];
  const label = useLevelLabel(level);
  const t = useT();
  const Icon = meta.icon;
  return (
    <span
      role="img"
      aria-label={`${t("common.urgencyLevelPrefix")}${label}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-md font-medium",
        meta.fg,
        meta.bg,
        size === "sm" ? "px-1.5 py-0.5 text-xs" : "px-2 py-1 text-sm",
      )}
      data-level={level}
    >
      <Icon aria-hidden="true" size={size === "sm" ? 12 : 14} />
      <span>{label}</span>
    </span>
  );
}
