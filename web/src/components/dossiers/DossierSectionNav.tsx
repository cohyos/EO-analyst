export interface DossierSectionNavItem {
  id: string;
  label: string;
}

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "sticky in-page section nav" for the detail
 * page's fixed section order (תקציר | מפרט | גרסאות | ... | מקורות). Plain anchor links -- each
 * section renders with a matching `id` and `scroll-mt-*` (so the sticky nav/header doesn't cover
 * the heading it jumps to) -- deliberately no active-section tracking/IntersectionObserver:
 * native anchor scrolling covers the "jump to a section" need this exists for without extra
 * complexity or a11y edge cases.
 */
export function DossierSectionNav({ items }: { items: DossierSectionNavItem[] }) {
  return (
    <nav
      aria-label="ניווט בין סעיפי הסקירה"
      // Mobile fix (UI-MOBILE-iphone.md #4): the sticky nav's translucent `bg-bg/95` +
      // `backdrop-blur` let the page header (product name, date) bleed through and read as
      // overlapping garbled text once it scrolled up underneath the nav -- worst on phones, where
      // the nav sits directly above that header with almost no clearance. An opaque `bg-bg`
      // removes the bleed-through entirely; chips (below) give each item its own visible
      // background so the row reads as a horizontally-scrollable tab strip rather than plain text
      // that could still visually run into whatever sits above it.
      className="sticky top-0 z-10 -mx-4 overflow-x-auto border-b border-border bg-bg px-4 py-2 md:-mx-6 md:px-6"
    >
      <ul className="flex list-none flex-nowrap gap-2 whitespace-nowrap text-xs">
        {items.map((item) => (
          <li key={item.id} className="shrink-0">
            <a
              href={`#${item.id}`}
              className="block rounded-full bg-bg-sunken px-2.5 py-1 text-fg-dim hover:bg-accent-muted hover:text-accent"
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
