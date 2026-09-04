import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function StatTile({
  label,
  value,
  icon,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "danger" | "warn" | "ok";
}) {
  const toneClass = {
    default: "text-fg",
    danger: "text-level-red",
    warn: "text-level-orange",
    ok: "text-ok",
  }[tone];

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-bg-raised p-3 shadow-panel">
      <div className="flex items-center gap-2 text-xs text-fg-dim">
        {icon}
        <span>{label}</span>
      </div>
      <p className={cn("font-mono font-tabular text-2xl font-semibold", toneClass)}>{value}</p>
    </div>
  );
}
