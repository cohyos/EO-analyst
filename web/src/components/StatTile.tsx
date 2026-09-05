import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/cn";

const TONE_CLASS = {
  default: "text-fg",
  danger: "text-level-red",
  warn: "text-level-orange",
  ok: "text-ok",
} as const;

interface StatTileProps {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "danger" | "warn" | "ok";
  /** U2: navigates here on click/Enter — renders as a `role="link"`-semantics `<Link>`. */
  to?: string;
  /** U2: alternative to `to` for a non-navigation action (e.g. opening a drawer). */
  onClick?: () => void;
  /** Screen-reader label for the interactive variants; falls back to `label`. */
  ariaLabel?: string;
}

const BASE_CLASS =
  "flex flex-col gap-1 rounded-lg border border-border bg-bg-raised p-3 shadow-panel";
const INTERACTIVE_CLASS = "text-start transition-colors hover:border-border-strong hover:bg-bg-sunken";

function TileContents({ label, value, icon, tone = "default" }: Omit<StatTileProps, "to" | "onClick" | "ariaLabel">) {
  return (
    <>
      <div className="flex items-center gap-2 text-xs text-fg-dim">
        {icon}
        <span>{label}</span>
      </div>
      <p className={cn("font-mono font-tabular text-2xl font-semibold", TONE_CLASS[tone])}>{value}</p>
    </>
  );
}

/**
 * U2 (docs/REVIEW_2026-09-05.md): every Morning KPI card is clickable and navigates to its
 * filtered view. Passing neither `to` nor `onClick` keeps the original plain, non-interactive
 * card (used elsewhere, e.g. EntitiesPage's KPI row, which shouldn't suddenly become a button).
 */
export function StatTile({ label, value, icon, tone = "default", to, onClick, ariaLabel }: StatTileProps) {
  if (to) {
    return (
      <Link to={to} aria-label={ariaLabel ?? label} className={cn(BASE_CLASS, INTERACTIVE_CLASS)}>
        <TileContents label={label} value={value} icon={icon} tone={tone} />
      </Link>
    );
  }
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-label={ariaLabel ?? label}
        className={cn(BASE_CLASS, INTERACTIVE_CLASS)}
      >
        <TileContents label={label} value={value} icon={icon} tone={tone} />
      </button>
    );
  }
  return (
    <div className={BASE_CLASS}>
      <TileContents label={label} value={value} icon={icon} tone={tone} />
    </div>
  );
}
