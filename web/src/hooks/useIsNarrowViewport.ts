import { useEffect, useState } from "react";

/**
 * Mobile fix (docs/qa/content_review/UI-MOBILE-iphone.md defect #1): `FeedRow` switches to a
 * 2-line layout below the `sm` (640px) breakpoint, which makes it taller than the single-line
 * desktop row. Feed lists render `FeedRow` inside a fixed-row-height virtualizer
 * (`useVirtualList`, `FeedPage.tsx`/`ProductLineDetailPage.tsx`'s `ROW_HEIGHT` constant) --
 * absolutely-positioned rows whose `top` offset assumes every row is the same height. This hook
 * tracks the `sm` breakpoint reactively (via `matchMedia`, not a one-time `window.innerWidth`
 * read) so those callers can pick a taller row slot on phones and keep the math in sync across
 * resizes/orientation changes, instead of silently overlapping rows.
 */
// Some environments (older jsdom versions used by the test runner included) expose a
// `window.matchMedia` property per the DOM IDL without actually implementing it as a callable --
// `"matchMedia" in window` is true but calling it throws "not a function". Guard on the actual
// type, not presence, so a test that doesn't stub `matchMedia` degrades to "not narrow" (the
// desktop layout) instead of crashing every component that calls this hook.
function hasMatchMedia(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

export function useIsNarrowViewport(maxWidthPx: number): boolean {
  const query = `(max-width: ${maxWidthPx - 1}px)`;
  const getMatches = () => (hasMatchMedia() ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(getMatches);

  useEffect(() => {
    if (!hasMatchMedia()) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  return matches;
}
