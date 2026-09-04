import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "./nav";
import { cn } from "@/lib/cn";

export function NavRail() {
  return (
    <nav
      aria-label="ניווט ראשי"
      className="flex w-16 shrink-0 flex-col items-center gap-1 border-l border-border bg-bg-raised py-3 md:w-48 md:items-stretch md:px-2"
    >
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-2.5 py-2.5 text-sm transition-colors md:px-3",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
                isActive
                  ? "bg-accent-muted text-accent-fg md:bg-accent/15 md:text-accent"
                  : "text-fg-muted hover:bg-bg-sunken hover:text-fg",
              )
            }
          >
            <Icon size={18} aria-hidden="true" className="shrink-0" />
            <span className="hidden truncate md:inline">{item.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
