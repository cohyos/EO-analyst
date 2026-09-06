import { NavLink } from "react-router-dom";
import { useNavItems } from "./nav";
import { useT } from "@/i18n";
import { cn } from "@/lib/cn";

export function NavRail() {
  const navItems = useNavItems();
  const t = useT();
  return (
    <nav
      aria-label={t("nav.ariaLabel")}
      // Full labeled rail only from `xl` (1280px, desktop) up — the whole
      // tablet band (768-1279px, portrait AND landscape) stays the narrow
      // icon rail so it doesn't eat into the 2-column tablet page layouts;
      // labels there surface as a hover/focus tooltip instead (below).
      className="flex w-16 shrink-0 flex-col items-center gap-1 border-l border-border bg-bg-raised py-3 xl:w-48 xl:items-stretch xl:px-2"
    >
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            aria-label={item.label}
            className={({ isActive }) =>
              cn(
                "tap-target group relative flex items-center justify-center gap-3 rounded-md px-2.5 py-2.5 text-sm transition-colors xl:justify-start xl:px-3",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
                isActive
                  ? "bg-accent-muted text-accent-fg xl:bg-accent/15 xl:text-accent"
                  : "text-fg-muted hover:bg-bg-sunken hover:text-fg",
              )
            }
          >
            <Icon size={18} aria-hidden="true" className="shrink-0" />
            <span className="hidden truncate xl:inline">{item.label}</span>
            {/* Tablet-only tooltip label (hidden on phones — no hover, and a
                tap navigates before it could be read; hidden again at `xl`
                where the persistent label above already shows). */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute start-full top-1/2 z-20 ms-2 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-border-strong bg-bg-raised px-2 py-1 text-xs text-fg opacity-0 shadow-panel transition-opacity sm:block group-hover:opacity-100 group-focus-visible:opacity-100 xl:hidden"
            >
              {item.label}
            </span>
          </NavLink>
        );
      })}
    </nav>
  );
}
