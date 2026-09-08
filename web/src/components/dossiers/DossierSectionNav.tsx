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
      className="sticky top-0 z-10 -mx-4 overflow-x-auto border-b border-border bg-bg/95 px-4 py-2 backdrop-blur supports-[backdrop-filter]:bg-bg/80 md:-mx-6 md:px-6"
    >
      <ul className="flex list-none gap-3 whitespace-nowrap text-xs">
        {items.map((item) => (
          <li key={item.id}>
            <a href={`#${item.id}`} className="text-fg-dim hover:text-accent hover:underline">
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
