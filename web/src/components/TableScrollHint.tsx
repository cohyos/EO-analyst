import { useT } from "@/i18n";

/**
 * Round-3 mobile fix (UI-MOBILE-iphone-r3.md #7): the one-line "there's more sideways" affordance
 * for a wide, `overflow-x-auto`-wrapped table -- same visual/markup contract as
 * `.report-table-scroll-hint` (globals.css, used by `wrapReportTables` for server-rendered report
 * tables) so every wide table in the app, report-rendered or component-rendered, gives the same
 * cue. `aria-hidden` since the wrapper itself already scrolls via a real, keyboard/AT-operable
 * mechanism (the table content itself, and the wrapper's own horizontal scrollbar) -- this is a
 * purely visual nudge, hidden again at `md:`+ where the mouse-visible scrollbar is enough on its
 * own (see `.report-table-scroll-hint`'s `@media (min-width: 768px)` rule).
 */
export function TableScrollHint() {
  const t = useT();
  return (
    <span className="report-table-scroll-hint" aria-hidden="true">
      {t("common.tableScrollHint")}
    </span>
  );
}
